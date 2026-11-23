from flask import Flask, request, jsonify, send_from_directory, send_file, abort, session
from pathlib import Path
from io import BytesIO
import sqlite3, uuid, json
from datetime import datetime, date
import os

# Mode produksi: PostgreSQL Supabase + Supabase Storage. Mode lokal tetap SQLite.
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').strip()
SUPABASE_PUBLISHABLE_KEY = (os.environ.get('SUPABASE_PUBLISHABLE_KEY') or os.environ.get('SUPABASE_ANON_KEY') or '').strip()
SUPABASE_SERVICE_ROLE_KEY = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_SECRET_KEY') or '').strip()
_configured = [bool(DATABASE_URL), bool(SUPABASE_URL), bool(SUPABASE_SERVICE_ROLE_KEY), bool(SUPABASE_PUBLISHABLE_KEY), bool(os.environ.get('FLASK_SECRET_KEY'))]
if any(_configured) and not all(_configured):
    raise RuntimeError('Konfigurasi belum lengkap: DATABASE_URL, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_PUBLISHABLE_KEY, dan FLASK_SECRET_KEY wajib diisi bersama-sama.')
if os.environ.get('RENDER') and not all(_configured):
    raise RuntimeError('Render wajib dikonfigurasi ke Supabase sebelum aplikasi dijalankan; mode SQLite lokal tidak diizinkan.')
USE_SUPABASE = all(_configured)
if USE_SUPABASE:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg import IntegrityError as PostgresIntegrityError
    from supabase import create_client
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    auth_client = create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)
else:
    PostgresIntegrityError = sqlite3.IntegrityError
    supabase = None
    auth_client = None

BASE = Path(__file__).resolve().parent
DATA = BASE / 'data'
UPLOADS = BASE / 'uploads'
DATA.mkdir(exist_ok=True); UPLOADS.mkdir(exist_ok=True)
DB = DATA / 'digital_mandiri.db'
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or 'local-development-only-change-me'
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=bool(USE_SUPABASE), MAX_CONTENT_LENGTH=20 * 1024 * 1024)

@app.before_request
def require_api_login():
    if not request.path.startswith('/api/') or request.path in ('/api/health', '/api/login'):
        return None
    user=session.get('user')
    if not user:
        return jsonify(error='Sesi login tidak ditemukan. Silakan login kembali.'), 401
    role=user.get('role')
    if request.path.startswith('/api/users') and request.method != 'GET' and role != 'admin':
        return jsonify(error='Aksi ini hanya dapat dilakukan oleh Admin.'), 403
    if request.path.endswith('/approve') and request.path.startswith('/api/dispositions/') and role != 'pemimpin':
        return jsonify(error='Persetujuan disposisi hanya dapat dilakukan oleh Pemimpin.'), 403
    if request.path == '/api/signature' and role != 'pemimpin':
        return jsonify(error='Tanda tangan hanya dapat diperbarui oleh Pemimpin.'), 403
    return None

CLASSIFICATIONS = [
 ('100','Bisnis','Perkenalan, permintaan penawaran, penawaran, pesanan, pengiriman, tagihan, klaim, kontrak, riset pasar, promosi, layanan pelanggan, distribusi, kunjungan bisnis'),
 ('200','Kepegawaian','Cuti, pendidikan, rekrutmen, mutasi, SDM'),
 ('300','Keuangan','Anggaran, perbankan, perpajakan, investasi, laporan keuangan'),
 ('400','Transportasi','Darat, laut, udara, logistik dan ekspedisi'),
 ('500','Humas & Publikasi','Siaran pers, brosur promosi'),
 ('600','Sarana & Prasarana','Pemeliharaan gedung, inventaris kantor, pengadaan alat'),
 ('700','Hukum & Perizinan','Domisili, surat kuasa hukum, perizinan'),
 ('800','Teknologi Informasi','Pengadaan software, laporan perbaikan IT, hak akses sistem'),
 ('900','Intern','Undangan, kuasa, keterangan, memo, nota, pengantar, permohonan, keputusan, tugas')]

def conn():
    if USE_SUPABASE:
        # Adapter kecil menjaga bentuk query lama Flask; placeholder SQLite (?) diubah ke PostgreSQL (%s).
        class PgCompat:
            def __enter__(self):
                self.raw = psycopg.connect(DATABASE_URL, row_factory=dict_row)
                return self
            def __exit__(self, typ, value, tb):
                try:
                    if typ: self.raw.rollback()
                    else: self.raw.commit()
                finally: self.raw.close()
                return False
            def execute(self, sql, params=()):
                # SQL aplikasi memakai placeholder ?; query yang digunakan tidak mengandung tanda tanya literal.
                return self.raw.execute(sql.replace('?', '%s'), params)
        return PgCompat()
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def now(): return datetime.now().isoformat(timespec='seconds')
def rowdict(r): return dict(r) if r else None

def init_db():
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS mails(id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('incoming','outgoing')), received_date TEXT, letter_date TEXT, number TEXT NOT NULL, party TEXT NOT NULL, subject TEXT NOT NULL, summary TEXT, code TEXT, sub_code TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'Diterima', pdf TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, agenda_number TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS classifications(code TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS custom_classifications(code TEXT PRIMARY KEY, parent_code TEXT NOT NULL, name TEXT NOT NULL, level TEXT NOT NULL, description TEXT DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS dispositions(id TEXT PRIMARY KEY, mail_id TEXT NOT NULL REFERENCES mails(id) ON DELETE CASCADE, completion_date TEXT, priority TEXT, instruction TEXT, recipients TEXT, notes TEXT, created_at TEXT NOT NULL, received_date TEXT DEFAULT '', subject TEXT DEFAULT '', letter_date TEXT DEFAULT '', number TEXT DEFAULT '', party TEXT DEFAULT '', attachment TEXT DEFAULT '', signature_date TEXT DEFAULT '', signer_name TEXT DEFAULT '', recipient1 TEXT DEFAULT '', recipient2 TEXT DEFAULT '', approval_status TEXT NOT NULL DEFAULT 'Menunggu ACC', approved_at TEXT DEFAULT '', approved_by TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS loans(id TEXT PRIMARY KEY, archive_code TEXT NOT NULL, archive_name TEXT NOT NULL, borrower TEXT NOT NULL, loan_date TEXT, due_date TEXT, status TEXT NOT NULL DEFAULT 'Menunggu persetujuan', notes TEXT, created_at TEXT NOT NULL, mail_id TEXT DEFAULT '', department TEXT DEFAULT '', returned_at TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, full_name TEXT NOT NULL, username TEXT NOT NULL UNIQUE, role TEXT NOT NULL, email TEXT, status TEXT NOT NULL DEFAULT 'Aktif', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, mail_number TEXT, activity TEXT NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL, details TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agenda_sequences(kind TEXT PRIMARY KEY, last_number INTEGER NOT NULL DEFAULT 0);
        ''')
        # Upgrade existing database safely when adding sub-classification support.
        cols={r['name'] for r in c.execute('PRAGMA table_info(mails)').fetchall()}
        if 'sub_code' not in cols: c.execute("ALTER TABLE mails ADD COLUMN sub_code TEXT DEFAULT ''")
        if 'agenda_number' not in cols: c.execute("ALTER TABLE mails ADD COLUMN agenda_number TEXT DEFAULT ''")
        # Backfill nomor agenda untuk surat lama tanpa mengubah data surat yang sudah ada.
        for mail_kind in ('incoming','outgoing'):
            existing=c.execute("SELECT id FROM mails WHERE kind=? AND COALESCE(agenda_number,'')='' ORDER BY COALESCE(received_date,''),created_at,id",(mail_kind,)).fetchall()
            seq=c.execute('SELECT last_number FROM agenda_sequences WHERE kind=?',(mail_kind,)).fetchone()
            last=int(seq['last_number']) if seq else 0
            for item in existing:
                last += 1
                c.execute('UPDATE mails SET agenda_number=? WHERE id=?',(f'{last:03d}',item['id']))
            c.execute('INSERT INTO agenda_sequences(kind,last_number) VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET last_number=MAX(last_number,excluded.last_number)',(mail_kind,last))
        lcols={r['name'] for r in c.execute('PRAGMA table_info(loans)').fetchall()}
        for col in ['mail_id','department','returned_at']:
            if col not in lcols: c.execute(f"ALTER TABLE loans ADD COLUMN {col} TEXT DEFAULT ''")
        dcols={r['name'] for r in c.execute('PRAGMA table_info(dispositions)').fetchall()}
        for col in ['received_date','subject','letter_date','number','party','attachment','signature_date','signer_name','recipient1','recipient2','recipient3','recipient4','approval_status','approved_at','approved_by']:
            if col not in dcols:
                default = "'Menunggu ACC'" if col == 'approval_status' else "''"
                c.execute(f"ALTER TABLE dispositions ADD COLUMN {col} TEXT DEFAULT {default}")
        for code,name,desc in CLASSIFICATIONS:
            c.execute('INSERT OR IGNORE INTO classifications VALUES(?,?,?)',(code,name,desc))
        for full,username,role,email in [('Mohd Satryawan Aras','m.satryawan','Pemimpin','direktur@digitalmandiri.co.id'),('Administrator','admin','Admin','admin@digitalmandiri.co.id'),('Nazwa Azzahra','nazwa.az','Sekretaris','sekretaris@digitalmandiri.co.id')]:
            c.execute('INSERT OR IGNORE INTO users(id,full_name,username,role,email,status,created_at) VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),full,username,role,email,'Aktif',now()))
        # Data awal peminjaman yang terhubung ke surat pada Buku Agenda.
        # INSERT OR IGNORE membuat seed aman dijalankan berulang tanpa menggandakan data.
        sample_specs = [
            ('223e2804-6cf0-40da-9d8d-52d11a96d55a', 'Byun Baekhyun', 'Bagian Administrasi', '2026-10-20', '2026-10-23', 'Dikembalikan', 'Peminjaman untuk verifikasi data surat dan penyusunan laporan.'),
            ('22229a77-ec6c-4450-8634-7e07f982e6a2', 'Park Chanyeol', 'Bagian Operasional', '2026-11-05', '2026-11-12', 'Dipinjam', 'Arsip dipinjam untuk pemeriksaan dokumen internal.'),
            ('0d30ddfe-0f10-4bbe-9fc0-978c665406a1', 'Park Jinyoung', 'Bagian SDM', '2026-11-09', '2026-11-16', 'Menunggu persetujuan', 'Pengajuan untuk keperluan administrasi dan pemeriksaan arsip.'),
            ('c97563ea-ffbe-4146-b51e-a01c0a96ee09', 'Keonho', 'Bagian Keuangan', '2026-11-07', '2026-11-14', 'Disetujui', 'Arsip diperlukan untuk pemeriksaan penawaran dan administrasi keuangan.'),
            ('1e6ec1fc-9e02-4e31-9d2b-8d9279d635d0', 'Jeong Jaehyun', 'Bagian Pengadaan', '2026-11-08', '2026-11-15', 'Dipinjam', 'Arsip dipinjam untuk membandingkan penawaran produk.')
        ]
        for mail_id, borrower, department, loan_date, due_date, status, notes in sample_specs:
            mail = c.execute('SELECT * FROM mails WHERE id=?', (mail_id,)).fetchone()
            if mail:
                lid = 'sample-loan-' + mail_id
                c.execute('''INSERT OR IGNORE INTO loans
                    (id,archive_code,archive_name,borrower,loan_date,due_date,status,notes,created_at,mail_id,department,returned_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (lid, mail['code'] or mail['sub_code'] or '-', mail['subject'] or mail['number'],
                     borrower, loan_date, due_date, status, notes, now(), mail_id, department,
                     loan_date if status == 'Dikembalikan' else ''))
                if c.execute('SELECT changes()').fetchone()[0]:
                    c.execute('INSERT INTO history(at,mail_number,activity,actor,status,details) VALUES(?,?,?,?,?,?)',
                        (now(), mail['number'], 'Peminjaman arsip dicatat', 'Sistem', status, borrower + ' — ' + mail['subject']))


        # Bersihkan label demonstrasi dari keterangan peminjaman yang sudah ada tanpa menghapus datanya.
        c.execute("UPDATE loans SET notes=TRIM(REPLACE(REPLACE(notes,'DATA CONTOH —',''),'DATA CONTOH -','')) WHERE notes LIKE '%DATA CONTOH%'")
        c.execute("UPDATE history SET activity='Peminjaman arsip diajukan' WHERE activity='Peminjaman arsip dicatat'")
        c.execute("UPDATE history SET details=TRIM(REPLACE(REPLACE(details,'DATA CONTOH —',''),'DATA CONTOH -','')) WHERE details LIKE '%DATA CONTOH%'")


# Jangan pernah membuat ulang database SQLite saat berjalan di Render.
if not USE_SUPABASE:
    init_db()

def get_json_or_form():
    if request.is_json: return request.get_json(silent=True) or {}
    return request.form.to_dict()

def save_upload():
    f=request.files.get('lampiranPDF') or request.files.get('pdf_file')
    if not f or not f.filename: return None
    if not f.filename.lower().endswith('.pdf'): abort(400, 'Lampiran harus berupa file PDF.')
    name=f'{uuid.uuid4().hex}_{Path(f.filename).name.replace(" ","_")}'
    if USE_SUPABASE:
        try:
            supabase.storage.from_('arsip-pdf').upload(name, f.read(), file_options={'content-type':'application/pdf','upsert':'false'})
        except Exception as exc:
            app.logger.exception('Gagal mengunggah PDF ke Supabase Storage')
            abort(502, 'PDF gagal disimpan ke penyimpanan online. Periksa konfigurasi Supabase Storage.')
    else:
        f.save(UPLOADS/name)
    return 'uploads/'+name

@app.get('/')
def home(): return send_from_directory(BASE,'index.html')
@app.post('/api/login')
def api_login():
    if not USE_SUPABASE:
        return jsonify(error='Autentikasi online belum dikonfigurasi.'), 503
    data=request.get_json(silent=True) or {}
    username=str(data.get('username','')).strip()
    password=str(data.get('password',''))
    if not username or not password: return jsonify(error='Username dan kata sandi wajib diisi.'),400
    with conn() as c:
        user=c.execute('SELECT id,full_name,username,role,email,status FROM users WHERE lower(username)=lower(?) LIMIT 1',(username,)).fetchone()
    if not user or str(user['status']).lower() not in ('aktif','active') or not user.get('email'):
        return jsonify(error='Akun tidak ditemukan, tidak aktif, atau belum memiliki email.'),401
    try:
        auth_result=auth_client.auth.sign_in_with_password({'email':user['email'],'password':password})
        if not getattr(auth_result,'user',None): raise ValueError('Auth gagal')
    except Exception:
        return jsonify(error='Username atau kata sandi salah. Pastikan akun telah dibuat di Supabase Authentication.'),401
    session.clear()
    session['user']={'id':user['id'],'username':user['username'],'name':user['full_name'],'role':user['role'].lower() if user['role'] else ''}
    return jsonify({'ok':True,'user':session['user']})

@app.post('/api/logout')
def api_logout():
    session.clear()
    return jsonify({'ok':True})

@app.get('/api/me')
def api_me():
    if not session.get('user'): return jsonify(user=None),401
    return jsonify(user=session['user'])

@app.get('/api/health')
def health():
    try:
        with conn() as c: c.execute('SELECT 1').fetchone()
        return jsonify({'ok':True,'database':'Supabase PostgreSQL' if USE_SUPABASE else 'SQLite','storage':'Supabase Storage' if USE_SUPABASE else 'Local filesystem','persistent':bool(USE_SUPABASE)})
    except Exception:
        app.logger.exception('Health check database gagal')
        return jsonify({'ok':False,'database':'Supabase PostgreSQL' if USE_SUPABASE else 'SQLite','error':'Database belum dapat dihubungi. Periksa DATABASE_URL dan status project Supabase.'}),503

@app.get('/api/mails/<mid>/pdf')
def get_mail_pdf(mid):
    with conn() as c:
        row = c.execute('SELECT pdf FROM mails WHERE id=?', (mid,)).fetchone()
    if not row:
        return jsonify(error='Data surat tidak ditemukan.'), 404
    stored = str(row['pdf'] or '').strip().replace('\\', '/')
    if not stored:
        return jsonify(error='Surat ini belum memiliki file PDF yang tersimpan.'), 404
    if USE_SUPABASE:
        key = stored.split('/', 1)[1] if stored.startswith('uploads/') else stored
        try:
            content = supabase.storage.from_('arsip-pdf').download(key)
            return send_file(BytesIO(content), mimetype='application/pdf', as_attachment=request.args.get('download') == '1', download_name=Path(key).name)
        except Exception:
            app.logger.exception('PDF tidak ditemukan di Supabase Storage: %s', key)
            return jsonify(error='File PDF tidak ditemukan pada penyimpanan online.'), 404
    candidate = (BASE / stored.lstrip('/')).resolve()
    uploads_root = UPLOADS.resolve()
    try: candidate.relative_to(uploads_root)
    except ValueError: return jsonify(error='Lokasi PDF tidak valid.'), 400
    if not candidate.is_file(): return jsonify(error='File PDF surat tidak ditemukan pada folder uploads.'), 404
    return send_file(candidate, mimetype='application/pdf', as_attachment=request.args.get('download') == '1', download_name=candidate.name)


@app.get('/api/mails')
def list_mails():
    kind=request.args.get('type'); code=request.args.get('code'); search=request.args.get('q','').strip(); mid=request.args.get('id')
    with conn() as c:
        sql='SELECT * FROM mails WHERE 1=1'; args=[]
        if mid: sql+=' AND id=?'; args.append(mid)
        if kind in ('incoming','outgoing'): sql+=' AND kind=?'; args.append(kind)
        if code:
            selected=str(code).strip()
            if selected in ('100','200','300','400','500','600','700','800','900'):
                # Kelompok utama: hitung/cari semua surat dengan kode klasifikasi dalam rentang kelompok, baik tersimpan di kolom code maupun sub_code.
                lo=int(selected); hi=lo+99
                sql+=" AND (CAST(substr(code,1,3) AS INTEGER) BETWEEN ? AND ? OR CAST(substr(sub_code,1,3) AS INTEGER) BETWEEN ? AND ? OR code=? OR sub_code=?)"
                args += [lo,hi,lo,hi,selected,selected]
            else:
                sql+=' AND (code=? OR code LIKE ? OR sub_code=? OR sub_code LIKE ?)'
                args += [selected,selected+'.%',selected,selected+'.%']
        if search: sql+=' AND (agenda_number LIKE ? OR number LIKE ? OR party LIKE ? OR subject LIKE ? OR summary LIKE ? OR code LIKE ?)'; args += [f'%{search}%']*6
        sql+=' ORDER BY received_date DESC, created_at DESC'
        rows=[dict(r) for r in c.execute(sql,args).fetchall()]
    return jsonify(rows[0] if mid and rows else (None if mid else rows))

def normalize_mail(d,kind,old=None,pdf=None):
    old=old or {}
    return {'kind':kind,'received_date':d.get('tanggalTerima') or d.get('tanggalKirim') or d.get('received_date') or old.get('received_date',''), 'letter_date':d.get('tanggalSurat') or d.get('letter_date') or old.get('letter_date',''), 'number':(d.get('nomorSurat') or d.get('number') or old.get('number') or '').strip(), 'party':(d.get('dari') or d.get('kepada') or d.get('party') or old.get('party') or '').strip(), 'subject':(d.get('perihal') or d.get('subject') or old.get('subject') or '').strip(), 'summary':d.get('isiRingkas') or d.get('summary') or old.get('summary',''), 'code':(d.get('kodeArsip') or d.get('code') or old.get('code') or '').strip(), 'sub_code':(d.get('subKodeArsip') or d.get('sub_code') or old.get('sub_code') or '').strip(), 'status':d.get('status') or old.get('status') or ('Diterima' if kind=='incoming' else 'Draf'), 'pdf':pdf or old.get('pdf') or '', 'agenda_number':(d.get('agendaNumber') or d.get('agenda_number') or old.get('agenda_number') or '').strip()}

@app.post('/api/mails')
def create_mail():
    d=get_json_or_form(); kind=d.get('kind','incoming')
    if kind not in ('incoming','outgoing'): return jsonify(error='Jenis surat tidak valid'),400
    data=normalize_mail(d,kind,pdf=save_upload())
    if not data['number'] or not data['party'] or not data['subject']: return jsonify(error='Nomor surat, pihak, dan perihal wajib diisi.'),400
    mid=str(uuid.uuid4()); stamp=now()
    with conn() as c:
        c.execute('INSERT INTO agenda_sequences(kind,last_number) VALUES(?,0) ON CONFLICT(kind) DO NOTHING',(kind,))
        c.execute('UPDATE agenda_sequences SET last_number=last_number+1 WHERE kind=?',(kind,))
        seq=c.execute('SELECT last_number FROM agenda_sequences WHERE kind=?',(kind,)).fetchone()['last_number']
        data['agenda_number']=f'{int(seq):03d}'
        c.execute('INSERT INTO mails(id,kind,received_date,letter_date,number,party,subject,summary,code,sub_code,status,pdf,created_at,updated_at,agenda_number) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(mid,data['kind'],data['received_date'],data['letter_date'],data['number'],data['party'],data['subject'],data['summary'],data['code'],data['sub_code'],data['status'],data['pdf'],stamp,stamp,data['agenda_number']))
        c.execute('INSERT INTO history(at,mail_number,activity,actor,status,details) VALUES(?,?,?,?,?,?)',(stamp,data['number'],'Surat '+('masuk' if kind=='incoming' else 'keluar')+' dicatat',d.get('actor','Admin'),data['status'],data['summary']))
    return jsonify({'id':mid,**data}),201

@app.put('/api/mails/<mid>')
@app.patch('/api/mails/<mid>')
def update_mail(mid):
    d=get_json_or_form()
    with conn() as c:
        old=c.execute('SELECT * FROM mails WHERE id=?',(mid,)).fetchone()
        if not old: return jsonify(error='Surat tidak ditemukan'),404
        old=dict(old); data=normalize_mail(d,old['kind'],old,save_upload())
        # Nomor agenda dibuat otomatis saat input, tetapi dapat dikoreksi dari form edit.
        agenda_no=data['agenda_number'] or old.get('agenda_number') or ''
        c.execute('UPDATE mails SET received_date=?,letter_date=?,number=?,party=?,subject=?,summary=?,code=?,sub_code=?,status=?,pdf=?,agenda_number=?,updated_at=? WHERE id=?',(data['received_date'],data['letter_date'],data['number'],data['party'],data['subject'],data['summary'],data['code'],data['sub_code'],data['status'],data['pdf'],agenda_no,now(),mid))
        data['agenda_number']=agenda_no
        c.execute('INSERT INTO history(at,mail_number,activity,actor,status,details) VALUES(?,?,?,?,?,?)',(now(),data['number'],'Data surat diperbarui',d.get('actor','Admin'),data['status'],data['summary']))
    return jsonify({'id':mid,**data})

@app.delete('/api/mails/<mid>')
def delete_mail(mid):
    with conn() as c:
        old=c.execute('SELECT * FROM mails WHERE id=?',(mid,)).fetchone()
        if not old: return jsonify(error='Surat tidak ditemukan'),404
        c.execute('INSERT INTO history(at,mail_number,activity,actor,status,details) VALUES(?,?,?,?,?,?)',(now(),old['number'],'Surat dihapus','Admin','Dihapus','Penghapusan data surat'))
        c.execute('DELETE FROM mails WHERE id=?',(mid,))
    return jsonify(ok=True)

@app.get('/api/classifications')
def list_classes():
    with conn() as c:
        classes=[dict(r) for r in c.execute('SELECT code,name,description FROM classifications ORDER BY code').fetchall()]
        mails=[dict(r) for r in c.execute('SELECT code,sub_code FROM mails').fetchall()]
    def belongs(value, root):
        value=str(value or '').strip()
        if not value: return False
        if value == '1000': return root == '900'
        if not value[:3].isdigit(): return False
        n=int(value[:3])
        return int(root) <= n <= int(root)+99
    for item in classes:
        item['total']=sum(1 for m in mails if belongs(m.get('code'),item['code']) or belongs(m.get('sub_code'),item['code']))
    return jsonify(classes)

@app.get('/api/classification-items')
def list_classification_items():
    with conn() as c:
        rows=c.execute('SELECT code,parent_code,name,level,description FROM custom_classifications ORDER BY code').fetchall()
    return jsonify([dict(r) for r in rows])

@app.post('/api/classification-items')
def create_classification_item():
    d=request.get_json(silent=True) or {}
    code=str(d.get('code','')).strip()
    parent=str(d.get('parent_code','')).strip()
    name=str(d.get('name','')).strip()
    level=str(d.get('level','detail')).strip()
    description=str(d.get('description','')).strip()
    if not code or not parent or not name:
        return jsonify(error='Kode induk, kode klasifikasi, dan nama wajib diisi.'),400
    if not all(part.isdigit() for part in code.split('.')):
        return jsonify(error='Format kode hanya boleh angka dan titik, contoh 110.1.1.'),400
    if level not in ('sub','detail'):
        return jsonify(error='Tingkat klasifikasi tidak valid.'),400
    if parent in ('100','200','300','400','500','600','700','800','900'):
        if code[:1] != parent[:1] or len(code.split('.')) != 1:
            return jsonify(error='Kode sub-kategori harus berada dalam kelompok utama yang dipilih, contoh 110 untuk induk 100.'),400
    elif not code.startswith(parent+'.'):
        return jsonify(error=f'Kode turunan harus diawali kode induknya, contoh {parent}.1.'),400
    with conn() as c:
        exists=c.execute('SELECT code FROM classifications WHERE code=? UNION SELECT code FROM custom_classifications WHERE code=?',(code,code)).fetchone()
        if exists:
            return jsonify(error='Kode tersebut sudah digunakan.'),409
        # Kode induk dapat berupa kode bawaan maupun kode tambahan.
        c.execute('INSERT INTO custom_classifications(code,parent_code,name,level,description,created_at) VALUES(?,?,?,?,?,?)',(code,parent,name,level,description,now()))
    return jsonify({'code':code,'parent_code':parent,'name':name,'level':level,'description':description}),201

@app.get('/api/dispositions')
def list_dispositions():
    mid=request.args.get('mail_id')
    with conn() as c:
        if mid:
            row=c.execute('SELECT d.*,m.number AS mail_number,m.subject AS mail_subject,m.party AS mail_party FROM dispositions d JOIN mails m ON m.id=d.mail_id WHERE d.mail_id=? ORDER BY d.created_at DESC LIMIT 1',(mid,)).fetchone()
            return jsonify(dict(row) if row else None)
        rows=c.execute('SELECT d.*,m.number,m.subject,m.party FROM dispositions d JOIN mails m ON m.id=d.mail_id ORDER BY d.created_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])

@app.post('/api/dispositions')
def create_disposition():
    d=request.get_json(silent=True) or {}; mid=d.get('mail_id')
    if not mid: return jsonify(error='ID surat wajib diisi'),400
    with conn() as c:
        m=c.execute('SELECT * FROM mails WHERE id=?',(mid,)).fetchone()
        if not m: return jsonify(error='Surat tidak ditemukan'),404
        stamp=now(); status='Diproses'
        recipients=d.get('recipients',[]); recipients=', '.join(recipients) if isinstance(recipients,list) else str(recipients)
        old=c.execute('SELECT id,approval_status FROM dispositions WHERE mail_id=? ORDER BY created_at DESC LIMIT 1',(mid,)).fetchone()
        if old and old['approval_status']=='Disetujui':
            return jsonify(error='Disposisi sudah di-ACC dan dikunci. Perubahan memerlukan proses koreksi resmi.'),409
        fields=(d.get('completion_date',''),d.get('priority','B'),d.get('instruction',''),recipients,d.get('notes',''),d.get('received_date',m['received_date'] or ''),d.get('subject',m['subject'] or ''),d.get('letter_date',m['letter_date'] or ''),d.get('number',m['number'] or ''),d.get('party',m['party'] or ''),d.get('attachment',''),d.get('signature_date',''),d.get('signer_name','Mohd Satryawan Aras'),d.get('recipient1',''),d.get('recipient2',''),d.get('recipient3',''),d.get('recipient4',''))
        if old:
            did=old['id']
            c.execute("UPDATE dispositions SET completion_date=?,priority=?,instruction=?,recipients=?,notes=?,received_date=?,subject=?,letter_date=?,number=?,party=?,attachment=?,signature_date=?,signer_name=?,recipient1=?,recipient2=?,recipient3=?,recipient4=?,created_at=?,approval_status='Menunggu ACC',approved_at='',approved_by='' WHERE id=?",(*fields,stamp,did))
            activity='Disposisi diperbarui'
        else:
            did=str(uuid.uuid4())
            c.execute('INSERT INTO dispositions(id,mail_id,completion_date,priority,instruction,recipients,notes,created_at,received_date,subject,letter_date,number,party,attachment,signature_date,signer_name,recipient1,recipient2,recipient3,recipient4) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(did,mid,*fields[:5],stamp,*fields[5:]))
            activity='Disposisi dibuat'
        # Keep the editable surat details synchronized with the mail record.
        c.execute('UPDATE mails SET received_date=?,letter_date=?,number=?,party=?,subject=?,updated_at=? WHERE id=?',(d.get('received_date',m['received_date'] or ''),d.get('letter_date',m['letter_date'] or ''),d.get('number',m['number'] or ''),d.get('party',m['party'] or ''),d.get('subject',m['subject'] or ''),stamp,mid))
        c.execute('UPDATE mails SET status=?,updated_at=? WHERE id=?',(status,stamp,mid))
        c.execute('INSERT INTO history(at,mail_number,activity,actor,status,details) VALUES(?,?,?,?,?,?)',(stamp,d.get('number',m['number']),'Disposisi dibuat/diperbarui',d.get('actor','Sekretaris'),status,d.get('instruction','')))
    return jsonify({'id':did,'mail_id':mid,'status':status}),201

@app.patch('/api/dispositions/<did>/priority')
def update_disposition_priority(did):
    d=request.get_json(silent=True) or {}
    priority=str(d.get('priority','')).upper().strip()
    if priority not in ('P','B','R'):
        return jsonify(error='Prioritas harus P, B, atau R.'),400
    actor=(d.get('actor') or 'Pengguna').strip()
    with conn() as c:
        row=c.execute('SELECT d.*,m.number AS mail_number FROM dispositions d JOIN mails m ON m.id=d.mail_id WHERE d.id=?',(did,)).fetchone()
        if not row: return jsonify(error='Disposisi tidak ditemukan.'),404
        previous=row['priority'] or 'B'
        if previous == priority:
            return jsonify({'ok':True,'id':did,'priority':priority,'unchanged':True})
        stamp=now()
        c.execute('UPDATE dispositions SET priority=? WHERE id=?',(priority,did))
        c.execute('INSERT INTO history(at,mail_number,activity,actor,status,details) VALUES(?,?,?,?,?,?)',
                  (stamp,row['mail_number'],'Prioritas disposisi diperbarui',actor,row['approval_status'],f'{previous} menjadi {priority}'))
    return jsonify({'ok':True,'id':did,'priority':priority,'updated_at':stamp})

@app.patch('/api/dispositions/<did>/approve')
def approve_disposition(did):
    d=request.get_json(silent=True) or {}
    actor=(d.get('approved_by') or 'Mohd Satryawan Aras').strip()
    with conn() as c:
        row=c.execute('SELECT d.*,m.number AS mail_number FROM dispositions d JOIN mails m ON m.id=d.mail_id WHERE d.id=?',(did,)).fetchone()
        if not row: return jsonify(error='Disposisi tidak ditemukan'),404
        if row['approval_status']=='Disetujui':
            return jsonify({'ok':True,'id':did,'approval_status':'Disetujui','approved_at':row['approved_at'],'approved_by':row['approved_by'],'already_approved':True})
        stamp=now()
        c.execute("UPDATE dispositions SET approval_status='Disetujui', approved_at=?, approved_by=?, signature_date=CASE WHEN COALESCE(signature_date,'')='' THEN ? ELSE signature_date END, signer_name=CASE WHEN COALESCE(signer_name,'')='' THEN ? ELSE signer_name END WHERE id=?",(stamp,actor,stamp[:10],actor,did))
        c.execute('INSERT INTO history(at,mail_number,activity,actor,status,details) VALUES(?,?,?,?,?,?)',(stamp,row['mail_number'],'Disposisi disetujui oleh pemimpin',actor,'Disetujui','Tanda tangan pimpinan diaktifkan'))
    return jsonify({'ok':True,'id':did,'approval_status':'Disetujui','approved_at':stamp,'approved_by':actor})

@app.post('/api/signature')
def upload_leader_signature():
    f = request.files.get('signature')
    if not f or not f.filename:
        return jsonify(error='Pilih gambar tanda tangan terlebih dahulu.'), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in {'.png', '.jpg', '.jpeg'}:
        return jsonify(error='Format tanda tangan harus PNG atau JPG.'), 400
    if not f.mimetype.startswith('image/'):
        return jsonify(error='File yang diunggah harus berupa gambar.'), 400
    target = BASE / 'assets' / 'tanda-tangan-pimpinan.png'
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        import io
        image = Image.open(f.stream).convert('RGB')
        image.thumbnail((1800, 1000))
        image.save(target, format='PNG', optimize=True)
    except Exception:
        return jsonify(error='Gambar tidak dapat dibaca. Coba file PNG atau JPG lain.'), 400
    return jsonify(ok=True, message='Tanda tangan berhasil disimpan.')

@app.get('/api/loans')
def list_loans():
    with conn() as c:
        rows=c.execute("""SELECT l.*,m.number AS letter_number,m.subject AS mail_subject,m.kind AS mail_kind,
                         m.party AS mail_party,m.code AS mail_code,m.pdf AS mail_pdf
                         FROM loans l LEFT JOIN mails m ON m.id=l.mail_id
                         ORDER BY l.created_at DESC""").fetchall()
    return jsonify([dict(r) for r in rows])

@app.post('/api/loans')
def create_loan():
    d=request.get_json(silent=True) or {}
    if not d.get('mail_id') or not d.get('borrower') or not d.get('due_date'):
        return jsonify(error='Pilih arsip dari Buku Agenda, isi nama peminjam, dan tanggal pengembalian.'),400
    lid=str(uuid.uuid4()); stamp=now(); status='Menunggu persetujuan'
    with conn() as c:
        mail=c.execute('SELECT * FROM mails WHERE id=?',(d['mail_id'],)).fetchone()
        if not mail: return jsonify(error='Surat/arsip tidak ditemukan di Buku Agenda.'),404
        active=c.execute("SELECT id FROM loans WHERE mail_id=? AND status IN ('Disetujui','Dipinjam')",(d['mail_id'],)).fetchone()
        if active: return jsonify(error='Arsip ini sedang dipinjam dan belum dikembalikan.'),409
        archive_name=mail['subject'] or mail['number']; archive_code=mail['code'] or mail['sub_code'] or ''
        c.execute("INSERT INTO loans(id,archive_code,archive_name,borrower,loan_date,due_date,status,notes,created_at,mail_id,department) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (lid,archive_code,archive_name,d['borrower'],d.get('loan_date') or date.today().isoformat(),d['due_date'],status,d.get('notes',''),stamp,d['mail_id'],d.get('department','')))
        c.execute('INSERT INTO history(at,mail_number,activity,actor,status,details) VALUES(?,?,?,?,?,?)',
                  (stamp,mail['number'],'Peminjaman arsip diajukan',d['borrower'],status,archive_name))
    return jsonify({'id':lid,'archive_code':archive_code,'archive_name':archive_name,'borrower':d['borrower'],'department':d.get('department',''),'loan_date':d.get('loan_date') or date.today().isoformat(),'due_date':d['due_date'],'status':status,'mail_id':d['mail_id']}),201

@app.patch('/api/loans/<lid>')
def update_loan(lid):
    d=request.get_json(silent=True) or {}; allowed={'Menunggu persetujuan','Disetujui','Ditolak','Dipinjam','Dikembalikan'}
    with conn() as c:
        old=c.execute('SELECT * FROM loans WHERE id=?',(lid,)).fetchone()
        if not old:return jsonify(error='Peminjaman tidak ditemukan'),404
        status=d.get('status',old['status'])
        if status not in allowed:return jsonify(error='Status peminjaman tidak valid.'),400
        returned=now() if status=='Dikembalikan' else old['returned_at']
        borrower=d.get('borrower',old['borrower']); department=d.get('department',old['department']); loan_date=d.get('loan_date',old['loan_date']); due_date=d.get('due_date',old['due_date']); notes=d.get('notes',old['notes'])
        mail_id=d.get('mail_id',old['mail_id'])
        mail=c.execute('SELECT * FROM mails WHERE id=?',(mail_id,)).fetchone() if mail_id else None
        if mail_id and not mail:return jsonify(error='Surat/arsip tidak ditemukan di Buku Agenda.'),404
        archive_name=(mail['subject'] or mail['number']) if mail else old['archive_name']
        archive_code=(mail['code'] or mail['sub_code'] or '') if mail else old['archive_code']
        c.execute('UPDATE loans SET status=?,notes=?,returned_at=?,borrower=?,department=?,loan_date=?,due_date=?,mail_id=?,archive_name=?,archive_code=? WHERE id=?',
                  (status,notes,returned,borrower,department,loan_date,due_date,mail_id,archive_name,archive_code,lid))
        mailrow=c.execute('SELECT number FROM mails WHERE id=?',(mail_id,)).fetchone() if mail_id else None
        activity='Data peminjaman diperbarui' if any(k in d for k in ('borrower','department','loan_date','due_date','mail_id','notes')) else 'Status peminjaman diperbarui'
        c.execute('INSERT INTO history(at,mail_number,activity,actor,status,details) VALUES(?,?,?,?,?,?)',
                  (now(),mailrow['number'] if mailrow else '-',activity,d.get('actor','Admin'),status,archive_name))
    return jsonify(ok=True,status=status)

@app.delete('/api/loans/<lid>')
def delete_loan(lid):
    with conn() as c: c.execute('DELETE FROM loans WHERE id=?',(lid,))
    return jsonify(ok=True)

@app.get('/api/users')
def list_users():
    with conn() as c: return jsonify([dict(r) for r in c.execute('SELECT * FROM users ORDER BY role,full_name').fetchall()])
@app.post('/api/users')
def create_user():
    d=request.get_json(silent=True) or {}
    if not d.get('full_name') or not d.get('username') or not d.get('role'): return jsonify(error='Nama, username, dan peran wajib diisi'),400
    uid=str(uuid.uuid4())
    try:
        with conn() as c: c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',(uid,d['full_name'],d['username'],d['role'],d.get('email',''),d.get('status','Aktif'),now()))
    except (sqlite3.IntegrityError, PostgresIntegrityError): return jsonify(error='Username sudah digunakan'),409
    return jsonify({'id':uid,**d}),201
@app.patch('/api/users/<uid>')
def update_user(uid):
    d=request.get_json(silent=True) or {}
    with conn() as c:
        old=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not old:return jsonify(error='Pengguna tidak ditemukan'),404
        full=d.get('full_name',old['full_name']); username=d.get('username',old['username']); role=d.get('role',old['role']); email=d.get('email',old['email']); status=d.get('status',old['status'])
        try:c.execute('UPDATE users SET full_name=?,username=?,role=?,email=?,status=? WHERE id=?',(full,username,role,email,status,uid))
        except (sqlite3.IntegrityError, PostgresIntegrityError):return jsonify(error='Username sudah digunakan'),409
    return jsonify(ok=True)
@app.delete('/api/users/<uid>')
def delete_user(uid):
    with conn() as c: c.execute('DELETE FROM users WHERE id=?',(uid,))
    return jsonify(ok=True)

@app.get('/api/history')
def list_history():
    q=request.args.get('q','').strip()
    with conn() as c:
        if q: rows=c.execute('SELECT * FROM history WHERE mail_number LIKE ? OR activity LIKE ? OR actor LIKE ? OR status LIKE ? ORDER BY id DESC',(f'%{q}%',)*4).fetchall()
        else: rows=c.execute('SELECT * FROM history ORDER BY id DESC LIMIT 300').fetchall()
    return jsonify([dict(r) for r in rows])
@app.get('/api/settings/<key>')
def get_setting(key):
    with conn() as c:r=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return jsonify({'key':key,'value':r['value'] if r else ''})
@app.put('/api/settings/<key>')
def put_setting(key):
    d=request.get_json(silent=True) or {}; val=str(d.get('value',''))
    with conn() as c:c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,val))
    return jsonify({'key':key,'value':val})

@app.get('/<path:path>')
def static_files(path):
    target=(BASE/path).resolve()
    if not str(target).startswith(str(BASE.resolve())) or not target.is_file() or path.startswith('data/') or path.endswith(('.db','.py','.pyc')) or path in ('requirements.txt','run_windows.bat','run_mac_linux.sh'): abort(404)
    return send_from_directory(BASE,path)

if __name__=='__main__':
    if not USE_SUPABASE: init_db()
    print('\nPT Digital Mandiri - server database ' + ('Supabase PostgreSQL' if USE_SUPABASE else 'SQLite'))
    print('Buka browser: http://127.0.0.1:5000')
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
