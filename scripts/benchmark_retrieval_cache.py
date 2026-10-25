#!/usr/bin/env python3
"""Compare repeated synthetic retrieval with/without result caching; no answer export."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'matrix_support_bot'))
from evaluation_core import private_json
from search_cache import SearchCache


def benchmark(store, count=20):
    if not 4<=count<=40:
        raise ValueError('Bound synthetic retrieval workload to 4..40')
    workload=[('legal','Начальник не отдаёт деньги за работу') if i%2==0 else
              ('finance','Что такое ГЭСВ и чем отличается от номинальной ставки?') for i in range(count)]
    saved=store._search_results
    rows={};rankings={}
    def one(item):
        start=time.perf_counter();hits=store.search(*item,limit=8)
        return time.perf_counter()-start,[p['id'] for p in hits]
    try:
        for label,cache in [('without_result_cache',None),('with_result_cache',SearchCache())]:
            store._search_results=cache;started=time.perf_counter()
            with ThreadPoolExecutor(2) as pool:
                result=list(pool.map(one,workload))
            durations=sorted(t[0] for t in result);rankings[label]=[t[1] for t in result]
            rows[label]={'requests':count,'workers':2,'elapsed_seconds':round(time.perf_counter()-started,3),
                         'search_p95_seconds':round(durations[math.ceil(len(durations)*.95)-1],3),
                         'cache':cache.metrics() if cache else None}
    finally:
        store._search_results=saved
    return {'schema_version':1,'synthetic_queries':True,'no_model_calls':True,'no_matrix_messages':True,
            'identical_rankings':rankings['without_result_cache']==rankings['with_result_cache'],
            'semantic_loaded':store.semantic is not None,'cohorts':rows,
            'limitation':'Shared process, repeated public synthetic queries; not Matrix response latency or production SLA.'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--knowledge',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    from offline_knowledge import KnowledgeStore
    result=benchmark(KnowledgeStore(a.knowledge));private_json(a.output,result);print(json.dumps(result))
    if not result['identical_rankings']:raise RuntimeError('Cache changed retrieval rankings')
if __name__=='__main__':main()
