from PyInstaller.utils.hooks import collect_data_files

a = Analysis(['launch_web.py'], pathex=[], binaries=[],
             datas=collect_data_files('epitope_screening'),
             hiddenimports=['epitope_screening.web_app','epitope_screening.web_setup'],
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['matplotlib','IPython','pytest','tkinter'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Start-Web',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='EpitopeScreening-Web')
