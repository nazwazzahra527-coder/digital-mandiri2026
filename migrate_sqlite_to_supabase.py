#!/usr/bin/env python3
"""Migrasi idempoten SQLite Digital Mandiri ke Supabase PostgreSQL + Storage.
Jalankan lokal dari folder aplikasi setelah mengisi environment variables secara lokal.
"""
import os, sys, sqlite3, mimetypes
from pathlib import Path

try:
    import psycopg
    from psycopg.rows import dict_row
    from supabase import create_client
except ImportError as exc:
    raise SystemExit('Dependensi belum ada. Jalankan: pip install -r requirements.txt') from exc

BASE = Path(__file__).resolve().parent
DB = BASE / 'data' / 'digital_mandiri.db'
URL = os.environ.get('DATABASE_URL', '').strip()
SURL = os.environ.get('SUPABASE_URL', '').strip()
SKEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_SECRET_KEY') or '').strip()
BUCKET = 'arsip-pdf'
TABLES = {
 'classifications': ['code','name','description'],
 'custom_classifications': ['code','parent_code','name','level','description','created_at'],
 'mails': ['id','kind','received_date','letter_date','number','party','subject','summary','code','sub_code','status','pdf','created_at','updated_at','agenda_number'],
 'dispositions': ['id','mail_id','completion_date','priority','instruction','recipients','notes','created_at','received_date','subject','letter_date','number','party','attachment','signature_date','signer_name','recipient1','recipient2','recipient3','recipient4','approval_status','approved_at','approved_by'],
 'loans': ['id','archive_code','archive_name','borrower','loan_date','due_date','status','notes','created_at','mail_id','department','returned_at'],
 'users': ['id','full_name','username','role','email','status','created_at'],
 'history': ['id','at','mail_number','activity','actor','status','details'],
 'settings': ['key','value'],
 'agenda_sequences': ['kind','last_number'],
}
PK = {'classifications':'code','custom_classifications':'code','mails':'id','dispositions':'id','loans':'id','users':'id','history':'id','settings':'key','agenda_sequences':'kind'}

def main():
    if not DB.is_file(): raise SystemExit(f'Database sumber tidak ditemukan: {DB}')
    if not (URL and SURL and SKEY): raise SystemExit('Isi DATABASE_URL, SUPABASE_URL, dan SUPABASE_SERVICE_ROLE_KEY di environment lokal. Jangan masukkan secret ke GitHub/chat.')
    sb = create_client(SURL, SKEY)
    src = sqlite3.connect(DB); src.row_factory = sqlite3.Row
    with psycopg.connect(URL, row_factory=dict_row) as dst:
        # Upload semua PDF yang direferensikan oleh kolom mail/disposition.
        pdf_paths = set()
        for table, col in [('mails','pdf'), ('dispositions','attachment')]:
            for r in src.execute(f"SELECT {col} AS p FROM {table} WHERE COALESCE({col},'')<>''"):
                val = str(r['p'] or '').strip().replace('\\','/')
                if val and val != '-': pdf_paths.add(val)
        for rel in sorted(pdf_paths):
            local = (BASE / rel).resolve()
            try: local.relative_to(BASE.resolve())
            except ValueError: raise SystemExit(f'Path PDF di luar folder aplikasi: {rel}')
            if not local.is_file(): raise SystemExit(f'PDF yang direferensikan tidak ditemukan: {rel}')
            key = rel.split('/',1)[1] if rel.startswith('uploads/') else rel
            with local.open('rb') as fh: content = fh.read()
            sb.storage.from_(BUCKET).upload(key, content, file_options={'content-type':'application/pdf','upsert':'true'})
            print(f'PDF OK: {rel}')
        for table, cols in TABLES.items():
            rows = [dict(r) for r in src.execute(f'SELECT {", ".join(cols)} FROM {table}').fetchall()]
            if not rows:
                print(f'{table}: 0'); continue
            colsql = ','.join(cols)
            placeholders = ','.join(['%s']*len(cols))
            updates = [c for c in cols if c != PK[table]]
            conflict = f"ON CONFLICT ({PK[table]}) DO UPDATE SET " + ','.join(f'{c}=EXCLUDED.{c}' for c in updates) if updates else 'ON CONFLICT DO NOTHING'
            sql = f'INSERT INTO public.{table} ({colsql}) VALUES ({placeholders}) {conflict}'
            with dst.cursor() as cur:
                cur.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
            print(f'{table}: {len(rows)} baris dimigrasikan')
        # Sinkronkan sequence identity history setelah id lama dimasukkan eksplisit.
        dst.execute("SELECT setval(pg_get_serial_sequence('public.history','id'), COALESCE((SELECT MAX(id) FROM public.history),1), true)")
        dst.commit()
    src.close()
    print('MIGRASI SELESAI. Verifikasi jumlah baris dan coba unduh PDF sebelum aplikasi diaktifkan.')

if __name__ == '__main__': main()
