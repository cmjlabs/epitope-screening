from PyInstaller.utils.hooks import collect_data_files

a = Analysis(['launch.py'], pathex=[], binaries=[],
             datas=collect_data_files('epitope_screening'),
             hiddenimports=['epitope_screening.gui'], hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=['matplotlib', 'IPython', 'pytest'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='EpitopeScreening',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='EpitopeScreening')
