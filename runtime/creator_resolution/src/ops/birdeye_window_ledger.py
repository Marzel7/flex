"""Pure identity/resume rules for coexisting Birdeye evidence windows."""
from __future__ import annotations
def key(mint,interval,start,end,anchor,index):return f'{mint}|{interval}|{start}|{end}|{anchor}|{index}'
def next_window(mint,migration,interval,seconds,index,anchor='MIGRATION'):
 start=migration+seconds*index;return {'mint':mint,'interval':interval,'start':start,'end':start+seconds,'window_anchor':anchor,'window_index':index,'identity':key(mint,interval,start,start+seconds,anchor,index)}
def eligible(record):return record is None or record.get('status') not in {'SUCCESS','RATE_LIMITED'}
