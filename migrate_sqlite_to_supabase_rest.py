#!/usr/bin/env python3
"""Migrate Digital Mandiri SQLite data + PDFs using Supabase HTTPS API only.
Avoids psycopg/libpq, which may be blocked by Windows Application Control.
Run from the extracted application folder; this file may be copied to that folder.
"""
import os, sys, sqlite3
from pathlib import Path
from supabase import create_client

BASE = Path(__file__).resolve().parent
DB = BASE / 'data' / 'digital_mandiri.db'
BUCKET = 'arsip-pdf'
URL = (os.getenv('SUPABASE_URL') or '').strip()
KEY = (os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_SECRET_KEY') or '').strip()

TABLES = {
    'classifications': (['code','name','description'], 'code'),
    'custom_classifications': (['code','parent_code','name','level','description','created_at'], 'code'),
    'mails': (['id','kind','received_date','letter_date','number','party','subject','summary','code','sub_code','status','pdf','created_at','updated_at','agenda_number'], 'id'),
    'dispositions': (['id','mail_id','completion_date','priority','instruction','recipients','notes','created_at','received_date','subject','letter_date','number','party','attachment','signature_date','signer_name','recipient1','recipient2','recipient3','recipient4','approval_status','approved_at','approved_by'], 'id'),
    'loans': (['id','archive_code','archive_name','borrower','loan_date','due_date','status','notes','created_at','mail_id','department','returned_at'], 'id'),
    'users': (['id','full_name','username','role','email','status','created_at'], 'id'),
    'history': (['id','at','mail_number','activity','actor','status','details'], 'id'),
    'settings': (['key','value'], 'key'),
    'agenda_sequences': (['kind','last_number'], 'kind'),
}


def main():
    if not DB.is_file():
        sys.exit(f'Database sumber tidak ditemukan: {DB}')
    if not URL or not KEY:
        sys.exit('Isi SUPABASE_URL dan SUPABASE_SERVICE_ROLE_KEY di PowerShell terlebih dahulu.')

    src = sqlite3.connect(DB)
    src.row_factory = sqlite3.Row
    counts = {t: src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in TABLES}
    print('DATA SUMBER (SQLite):')
    for t, n in counts.items(): print(f'  {t}: {n}')

    # Check every target table first. Do not write if any target table already has rows.
    sb = create_client(URL, KEY)
    nonempty = []
    for table in TABLES:
        try:
            result = sb.table(table).select('*').limit(1).execute()
            if result.data:
                nonempty.append(table)
        except Exception as e:
            src.close()
            sys.exit(f'Gagal memeriksa tabel {table}: {e}')
    if nonempty:
        src.close()
        sys.exit('DIBATALKAN demi keamanan. Tabel tujuan sudah berisi data: ' + ', '.join(nonempty) + '. Jangan jalankan upsert sebelum diperiksa.')

    # Upload every referenced PDF before inserting rows.
    pdf_paths = set()
    for table, col in [('mails','pdf'), ('dispositions','attachment')]:
        for r in src.execute(f"SELECT {col} AS p FROM {table} WHERE COALESCE({col},'')<>''"):
            rel = str(r['p'] or '').strip().replace('\\','/')
            if rel and rel != '-': pdf_paths.add(rel)
    for rel in sorted(pdf_paths):
        local = (BASE / rel).resolve()
        try: local.relative_to(BASE.resolve())
        except ValueError:
            src.close(); sys.exit(f'Path PDF di luar folder aplikasi: {rel}')
        if not local.is_file():
            src.close(); sys.exit(f'PDF yang direferensikan tidak ditemukan: {rel}')
        key = rel.split('/',1)[1] if rel.startswith('uploads/') else rel
        try:
            sb.storage.from_(BUCKET).upload(key, local.read_bytes(), file_options={'content-type':'application/pdf','upsert':'false'})
            print(f'PDF OK: {rel}')
        except Exception as e:
            src.close(); sys.exit(f'Gagal mengunggah PDF {rel}: {e}')

    # Insert parent tables before dependent tables. Target was confirmed empty above.
    for table, (cols, pk) in TABLES.items():
        rows = [dict(r) for r in src.execute(f'SELECT {", ".join(cols)} FROM "{table}"').fetchall()]
        if not rows:
            print(f'{table}: 0 baris'); continue
        try:
            for start in range(0, len(rows), 100):
                sb.table(table).upsert(rows[start:start+100], on_conflict=pk).execute()
            print(f'{table}: {len(rows)} baris berhasil dikirim')
        except Exception as e:
            src.close(); sys.exit(f'Gagal memasukkan tabel {table}: {e}')
    src.close()
    print('\nMIGRASI DATA DAN UPLOAD PDF SELESAI.')
    print('Langkah lanjutan: reset sequence history melalui SQL Editor, lalu verifikasi jumlah setiap tabel dan file Storage.')

if __name__ == '__main__':
    main()
