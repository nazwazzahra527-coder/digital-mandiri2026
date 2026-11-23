import sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=ROOT/'data'/'digital_mandiri.db'
if not DB.exists(): raise SystemExit(f'Database tidak ditemukan: {DB}')
c=sqlite3.connect(DB)
for t in ['classifications','custom_classifications','mails','dispositions','loans','users','history','settings','agenda_sequences']:
    print(f'{t}: {c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]} baris')
missing=[]; found=[]
for table,col in [('mails','pdf'),('dispositions','attachment')]:
    for (raw,) in c.execute(f"SELECT {col} FROM {table} WHERE COALESCE({col},'') NOT IN ('','-')"):
        rel=str(raw).replace('\\','/').lstrip('/')
        candidates=[ROOT/rel, ROOT/'uploads'/Path(rel).name, ROOT/'lampiran-surat-masuk'/Path(rel).name]
        match=next((p for p in candidates if p.is_file()),None)
        (found if match else missing).append((table,rel,str(match) if match else ''))
print(f'File PDF ditemukan: {len(found)}')
print(f'File PDF belum ditemukan: {len(missing)}')
for t,r,_ in missing: print(f'MISSING [{t}] {r}')
