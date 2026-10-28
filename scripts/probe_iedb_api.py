"""Live smoke test using only the public LCMV example from IEDB docs.

Run: python scripts/probe_iedb_api.py --output /tmp/iedb-api-live
A proxy is optional and applies to this process only. TLS verification stays on.
Saved job IDs allow --resume without submitting another prediction.
"""
import argparse
import csv
import http.client
import socket
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

BASE = 'https://api-nextgen-tools.iedb.org/api/v1'
PUBLIC_FASTA = '>LCMV Armstrong, Protein GP\nMGQIVTMFEALPHIIDEVINIVIIVLIVITGI'
CASES = {
    'mhci': {'alleles': 'HLA-A*02:01', 'peptide_length_range': [9, 9],
             'predictors': [{'type': 'binding', 'method': 'netmhcpan_ba'},
                            {'type': 'immunogenicity', 'mask_choice': 'default'}]},
    'mhcii': {'alleles': 'HLA-DRB1*01:01', 'peptide_length_range': [15, 15],
              'peptide_spacing': 1,
              'predictors': [{'type': 'binding', 'method': 'netmhciipan_ba-4.3'},
                             {'type': 'immunogenicity', 'method': 'cd4episcore'}]},
}


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def request_json(opener, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(BASE + path, data=data,
        headers={'Accept': 'application/json', 'Content-Type': 'application/json',
                 'User-Agent': 'EpitopeScreening-API-SmokeTest/0.1'})
    with opener.open(request, timeout=45) as response:
        return json.load(response)


def export_tables(result, folder):
    summary = []
    for index, table in enumerate(result.get('data', {}).get('results', [])):
        columns = table.get('table_columns', [])
        names = ['.'.join(filter(None, [c.get('source', ''), c.get('name', '')])) if isinstance(c, dict) else c for c in columns]
        path = folder / f"table_{index}_{table.get('type', 'unknown')}.csv"
        with path.open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.writer(stream)
            writer.writerow(names)
            writer.writerows(table.get('table_data', []))
        summary.append({'type': table.get('type'), 'rows': len(table.get('table_data', [])), 'columns': names})
    return summary


class InterfaceHTTPSConnection(http.client.HTTPSConnection):
    """Optional diagnostic transport; retains normal TLS hostname verification."""
    def __init__(self, host, interface=None, resolve_ip=None, **kwargs):
        super().__init__(host, **kwargs)
        def connect_socket(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
            hostname, port = address
            if hostname != 'api-nextgen-tools.iedb.org':
                raise ValueError('Diagnostic transport only supports the official IEDB API host')
            addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (resolve_ip, port))] if resolve_ip else socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            last_error = None
            for family, kind, proto, _, target in addresses:
                sock = socket.socket(family, kind, proto)
                try:
                    sock.settimeout(timeout)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b'\0')
                    sock.connect(target)
                    return sock
                except OSError as error:
                    last_error = error
                    sock.close()
            raise last_error
        self._create_connection = connect_socket


class InterfaceHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, interface, resolve_ip):
        super().__init__()
        self.interface, self.resolve_ip = interface, resolve_ip

    def https_open(self, request):
        def connection(host, **kwargs):
            return InterfaceHTTPSConnection(host, interface=self.interface, resolve_ip=self.resolve_ip, **kwargs)
        return self.do_open(connection, request, context=self._context)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--proxy', help='Optional HTTP proxy; no system settings are changed')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--interface', help='Linux-only diagnostic: bind this process to an interface')
    parser.add_argument('--resolve-ip', help='Diagnostic DNS override, retaining official hostname/SNI and TLS checks')
    parser.add_argument('--deadline', type=int, default=600)
    parser.add_argument('--case', choices=['mhci', 'mhcii', 'both'], default='both')
    args = parser.parse_args()
    proxy = urllib.request.ProxyHandler({'http': args.proxy, 'https': args.proxy}) if args.proxy else urllib.request.ProxyHandler()
    if args.interface:
        if args.proxy:
            parser.error('--interface and --proxy are mutually exclusive')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), InterfaceHTTPSHandler(args.interface, args.resolve_ip))
    else:
        if args.resolve_ip:
            parser.error('--resolve-ip requires --interface')
        opener = urllib.request.build_opener(proxy)
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'base_url': BASE,
              'sequence_source': 'https://nextgen-tools.iedb.org/docs/api/endpoints/', 'cases': {}}
    active = {}
    for name, parameters in CASES.items():
        if args.case != 'both' and args.case != name:
            continue
        folder = args.output / name
        folder.mkdir(exist_ok=True)
        submitted_path = folder / 'submitted.json'
        payload = {'pipeline_id': '', 'run_stage_range': [1, 1],
                   'stages': [{'stage_number': 1, 'tool_group': name, 'input_sequence_text': PUBLIC_FASTA,
                               'input_parameters': parameters}]}
        write_json(folder / 'request.json', payload)
        try:
            if args.resume and submitted_path.exists():
                response = json.loads(submitted_path.read_text(encoding='utf-8'))
            elif submitted_path.exists():
                raise ValueError('Existing submitted job; use --resume or a new output folder')
            else:
                response = request_json(opener, '/pipeline', payload)
                write_json(submitted_path, response)
            if response.get('errors') or not response.get('result_id'):
                raise ValueError(f'Prediction submission rejected: {response}')
            active[name] = response['result_id']
            print(f'{name}: submitted {response["result_id"]}', flush=True)
            report['cases'][name] = {'result_id': response['result_id'], 'status': 'submitted'}
        except Exception as error:
            report['cases'][name] = {'status': 'submission_failed', 'error': str(error)}
            print(f'{name}: {type(error).__name__}: {error}', flush=True)
    finish_by = time.monotonic() + args.deadline
    while active and time.monotonic() < finish_by:
        for name, result_id in list(active.items()):
            try:
                result = request_json(opener, '/results/' + result_id)
                write_json(args.output / name / 'result.json', result)
                status = result.get('status', 'unknown')
                print(f'{name}: {status}', flush=True)
                report['cases'][name]['status'] = status
                errors = result.get('data', {}).get('errors', [])
                if status == 'done':
                    tables = export_tables(result, args.output / name)
                    # Do not treat an empty or partly failed response as a successful prediction.
                    columns = [c for t in tables if t['type'] == 'peptide_table' and t['rows'] for c in t['columns']]
                    expected = ('binding.netmhcpan_ba', 'immunogenicity') if name == 'mhci' else ('binding.netmhciipan_ba-4.3', 'immunogenicity.cd4episcore')
                    present = {expected[0]: any(expected[0].removeprefix('binding.') + '_ic50' in c or c.startswith(expected[0] + '.') for c in columns),
                               expected[1]: any(c.startswith('immunogenicity.') and (c.endswith('.score') or 'cd4episcore' in c) for c in columns)}
                    report['cases'][name].update(tables=tables, method_columns_present=present, errors=errors,
                                                passed=not errors and all(present.values()))
                    active.pop(name)
                elif status in ('error', 'failed', 'failure', 'cancelled', 'canceled') or errors:
                    report['cases'][name].update(errors=errors, passed=False)
                    active.pop(name)
            except Exception as error:
                report['cases'][name]['last_poll_error'] = str(error)
                print(f'{name}: poll {type(error).__name__}: {error}', flush=True)
            write_json(args.output / 'verification_summary.json', report)
        if active:
            time.sleep(10)
    for name in active:
        report['cases'][name].update(status='timed_out', passed=False)
    report['passed'] = bool(report['cases']) and all(c.get('passed', False) for c in report['cases'].values())
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    write_json(args.output / 'verification_summary.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
