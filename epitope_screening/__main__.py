import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from . import __version__
from .core import Settings, run_analysis, populations


def main():
    # Redirected Windows streams can otherwise use a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description='MHC epitope prediction and screening for Windows and Linux')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command')
    gui = commands.add_parser('gui', help='Open desktop application')
    gui.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    commands.add_parser('populations', help='List names in the bundled frequency snapshot')
    commands.add_parser('default-config', help='Print default screening settings')
    commands.add_parser('default-prediction-config', help='Print default IEDB prediction settings')
    run = commands.add_parser('run', help='Screen existing IEDB prediction tables')
    for name in ('fasta', 'mhci', 'mhcii'):
        run.add_argument('--' + name, required=True, type=Path)
    run.add_argument('--output', type=Path, default=Path('results'))
    run.add_argument('--config', type=Path)
    run.add_argument('--cutoff', type=Path)
    online = commands.add_parser('online', help='FASTA to IEDB online predictions and local screening')
    online.add_argument('--fasta', required=True, type=Path)
    online.add_argument('--output', type=Path, default=Path('results'))
    online.add_argument('--config', type=Path, help='Local screening settings JSON')
    online.add_argument('--prediction-config', type=Path, help='PredictionSettings JSON')
    online.add_argument('--cutoff', type=Path)
    online.add_argument('--resume', type=Path, help='Existing online task directory')
    online.add_argument('--proxy', help='Optional HTTP proxy URL without credentials')
    local = commands.add_parser('local', help='Linux local IEDB TC1/TC2 prediction and screening')
    local.add_argument('--fasta', required=True, type=Path)
    local.add_argument('--tools-config', type=Path, default=Path('local-tools.json'))
    local.add_argument('--prediction-config', type=Path)
    local.add_argument('--config', type=Path)
    local.add_argument('--cutoff', type=Path)
    local.add_argument('--output', type=Path, default=Path('results'))
    local.add_argument('--resume', type=Path)
    web = commands.add_parser('web', help='Open the local browser application')
    web.add_argument('--data-dir', type=Path)
    web.add_argument('--port', type=int, default=0)
    web.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if args.command in (None, 'gui'):
        from .gui import main as gui_main
        gui_main(smoke_test=getattr(args, 'smoke_test', False))
    elif args.command == 'web':
        from .web_app import main as web_main
        options = ['--port',str(args.port)]
        if args.data_dir:
            options += ['--data-dir',str(args.data_dir)]
        if args.no_browser:
            options += ['--no-browser']
        web_main(options)
    elif args.command == 'populations':
        print('\n'.join(populations()))
    elif args.command == 'default-prediction-config':
        from .iedb_api import PredictionSettings
        print(json.dumps(asdict(PredictionSettings()), indent=2))
    elif args.command == 'default-config':
        print(json.dumps(asdict(Settings()), indent=2))
    else:
        try:
            config = json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config else {}
            settings = Settings(**config)
            progress = lambda percent, message: print(f'[{percent:3d}%] {message}', flush=True)
            if args.command == 'local':
                from .iedb_api import PredictionSettings
                from .local_iedb import run_local
                prediction = PredictionSettings(**(json.loads(args.prediction_config.read_text(encoding='utf-8-sig')) if args.prediction_config else {}))
                tools_config = json.loads(args.tools_config.read_text(encoding='utf-8-sig'))
                run_local(args.fasta, args.output, tools_config, settings, prediction, args.cutoff, progress=progress, resume=args.resume)
            elif args.command == 'online':
                from .iedb_api import PredictionSettings, run_online
                prediction = PredictionSettings(**(json.loads(args.prediction_config.read_text(encoding='utf-8-sig')) if args.prediction_config else {}))
                if args.proxy is not None:
                    prediction.proxy_url = args.proxy
                run_online(args.fasta, args.output, settings, prediction, args.cutoff, progress=progress, resume=args.resume)
            else:
                run_analysis(args.fasta, args.mhci, args.mhcii, args.output, settings, args.cutoff, progress=progress)
        except (ValueError, TypeError, OSError) as error:
            print(f'Error: {error}', file=sys.stderr)
            return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
