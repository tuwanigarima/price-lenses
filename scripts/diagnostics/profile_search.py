"""Time the existing graph without changing its nodes or execution policy.

Uses configured external services, including the current price-signal search
in database_only mode. Logs timings/status only, not prompts or credentials.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('query')
    parser.add_argument('--mode', choices=['database_only', 'database_first', 'api_first'], default='database_only')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/diagnostics/search-latency.json')
    args = parser.parse_args()
    import orchestrator
    from langchain_core.callbacks import BaseCallbackHandler

    started = perf_counter()
    spans = []
    active = {}

    class Timings(BaseCallbackHandler):
        def on_chain_start(self, serialized, inputs, *, run_id, metadata=None, **kwargs):
            metadata = metadata or {}
            name = kwargs.get('name')
            if name in {'input_resolver', 'history_agent', 'market_agent', 'policy_agent', 'decision_synthesizer', 'verifier_gate'}:
                active[run_id] = (name, perf_counter())

        def on_chain_end(self, outputs, *, run_id, **kwargs):
            if run_id in active:
                name, begin = active.pop(run_id)
                span = {'node': name, 'start_seconds': round(begin-started, 3),
                        'duration_seconds': round(perf_counter()-begin, 3)}
                spans.append(span)
                print(json.dumps(span), flush=True)

    state = orchestrator.graph.invoke(
        {'query': args.query, 'market_provider_policy': args.mode,
         'force_market_refresh': args.mode == 'api_first'},
        {'recursion_limit': 15, 'callbacks': [Timings()]},
    )
    result = {'captured_at': datetime.now(timezone.utc).isoformat(), 'query': args.query,
              'mode': args.mode, 'runner': 'existing LangGraph; no UI rendering',
              'total_seconds': round(perf_counter()-started, 3), 'nodes': spans,
              'reports': {}}
    for key in ('history_report', 'market_report', 'policy_report'):
        report = state.get(key) or {}
        result['reports'][key] = {'status': report.get('status'), 'stages': [
            {k: event.get(k) for k in ('stage','tool','source','status','duration_ms')}
            for event in report.get('agent_trace') or []]}
    result['verdict'] = {k: (state.get('final_verdict') or {}).get(k)
                         for k in ('decision','_synthesis_mode','verification_status')}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'total_seconds': result['total_seconds'], 'output': str(args.output)}), flush=True)


if __name__ == '__main__':
    main()
