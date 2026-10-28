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
    parser = argparse.ArgumentParser(description='Offline MHC epitope screening for Windows and Linux')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('gui', help='Open desktop application')
    commands.add_parser('populations', help='List names in the bundled frequency snapshot')
    commands.add_parser('default-config', help='Print default screening settings')
    run = commands.add_parser('run', help='Screen existing IEDB prediction tables')
    for name in ('fasta', 'mhci', 'mhcii'):
        run.add_argument('--' + name, required=True, type=Path)
    run.add_argument('--output', type=Path, default=Path('results'))
    run.add_argument('--config', type=Path)
    run.add_argument('--cutoff', type=Path)
    args = parser.parse_args()
    if args.command in (None, 'gui'):
        from .gui import main as gui_main
        gui_main()
    elif args.command == 'populations':
        print('\n'.join(populations()))
    elif args.command == 'default-config':
        print(json.dumps(asdict(Settings()), indent=2))
    else:
        try:
            config = json.loads(args.config.read_text(encoding='utf-8-sig')) if args.config else {}
            settings = Settings(**config)
            run_analysis(args.fasta, args.mhci, args.mhcii, args.output, settings, args.cutoff,
                         progress=lambda percent, message: print(f'[{percent:3d}%] {message}', flush=True))
        except (ValueError, TypeError, OSError) as error:
            print(f'Error: {error}', file=sys.stderr)
            return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
