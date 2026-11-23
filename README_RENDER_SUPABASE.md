# Digital Mandiri — Render + Supabase

## Status paket
Backend Flask sudah disiapkan untuk dua mode: SQLite lokal dan Supabase PostgreSQL + Supabase Storage. Login demo berbasis localStorage sudah diganti dengan login Supabase Auth dan session cookie server. API data dilindungi sesi; persetujuan disposisi dan pengelolaan pengguna diberi pembatasan peran dasar.

**Belum dapat disebut deploy/terhubung sampai variabel rahasia diisi, migrasi berhasil, dan uji online dilakukan.** Paket ini tidak membawa kredensial rahasia.

## A. Siapkan Supabase (sekali)
1. Buka SQL Editor pada project Supabase.
2. Jalankan isi `supabase/schema.sql`. Perintahnya menggunakan `IF NOT EXISTS`/`ON CONFLICT` agar aman untuk tabel dan bucket yang sudah ada.
3. Pastikan Storage bucket `arsip-pdf` tetap private.
4. Di Authentication > Users, buat akun untuk setiap pengguna aktif. Gunakan email yang sama dengan kolom `email` pada tabel `public.users`. Atur kata sandi sementara, lalu minta pengguna menggantinya.
5. Catat Project URL, Publishable key, server-side Secret/Service Role key, dan database connection string. Jangan masukkan Secret/Service Role key ke frontend atau GitHub.

## B. Migrasikan data dan PDF lama (sebelum deploy)
1. Ekstrak seluruh folder paket ini di komputer.
2. Pastikan `data/digital_mandiri.db`, `uploads/`, dan `lampiran-surat-masuk/` ikut berada dalam folder aplikasi.
3. Install Python 3.11+ lalu jalankan `pip install -r requirements.txt`.
4. Atur environment variables lokal (PowerShell contoh):

```powershell
$env:DATABASE_URL="postgresql://..."
$env:SUPABASE_URL="https://PROJECT.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY="SERVER_SIDE_SECRET"
python migrate_sqlite_to_supabase.py
```

Gunakan connection string PostgreSQL Supabase yang sesuai (disarankan Session Pooler bila jaringan/IPv4 direct connection bermasalah). Migrator bersifat upsert sehingga dapat dijalankan ulang, tetapi selalu periksa jumlah data dan file setelah selesai. Jangan mengirimkan kredensial rahasia ke chat atau commit ke GitHub.

## C. Upload kode ke GitHub
Upload isi folder paket ini ke root repository `digital-mandiri`, bukan folder bertingkat. Jangan upload `.env` atau file kredensial. `data/digital_mandiri.db` diperlukan untuk migrasi; simpan salinan cadangan lokal dan jangan hapus sampai verifikasi migrasi selesai.

## D. Render Web Service
- Root Directory: kosong
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn --bind 0.0.0.0:$PORT server:app`

Tambahkan Environment Variables berikut di Render:
- `DATABASE_URL` — connection string PostgreSQL Supabase
- `SUPABASE_URL` — Project URL
- `SUPABASE_SERVICE_ROLE_KEY` — server-side secret/service-role key
- `SUPABASE_PUBLISHABLE_KEY` — publishable key (untuk Supabase Auth sign-in)
- `FLASK_SECRET_KEY` — string acak panjang, unik, minimal 32 karakter

Aplikasi sengaja menolak start di Render bila konfigurasi belum lengkap, agar tidak diam-diam memakai SQLite sementara.

## E. Uji sebelum dibagikan ke dosen
1. Buka `/api/health`, pastikan `database` menunjukkan `Supabase PostgreSQL`, `storage` menunjukkan `Supabase Storage`, dan `persistent` bernilai `true`.
2. Login menggunakan akun yang sudah dibuat di Supabase Authentication.
3. Uji tambah/edit/hapus surat, tambah disposisi, persetujuan oleh Pemimpin, peminjaman, riwayat, dan unduh PDF.
4. Restart/redeploy service lalu pastikan data dan PDF tetap tersedia.
5. Baru bagikan URL Render kepada dosen.

## Catatan keamanan dan batasan
- User metadata pada `public.users` bukan akun login. Akun autentikasi harus dibuat di Supabase Authentication dengan email yang sama.
- API memerlukan session login. Pembatasan peran backend yang disiapkan mencakup persetujuan disposisi oleh Pemimpin dan pengelolaan pengguna oleh Admin; lakukan pengujian per peran sebelum pemakaian resmi.
- Tanda tangan yang diunggah melalui endpoint `/api/signature` masih disimpan pada folder aset aplikasi. Untuk perubahan tanda tangan yang harus bertahan setelah redeploy, pindahkan penyimpanannya ke Supabase Storage sebelum penggunaan produksi.
- Koneksi database, upload storage, dan login Supabase belum bisa diuji dari paket lokal tanpa kredensial project milik pemilik akun.
