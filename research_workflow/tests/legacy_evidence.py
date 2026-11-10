"""Offline decoder for retained historical unit fixtures; never a runtime dependency."""
import copy

def evidence_records(packet):
 """Read historical columnar evidence retained by evaluation tools."""
 if packet.get('representation')!='lossless-record-columns-v1' or len(packet['rows'])!=packet['record_count']:raise ValueError('Invalid evidence transport.')
 result=[]
 for row in packet['rows']:
  if len(row)!=len(packet['columns']):raise ValueError('Incomplete evidence row.')
  record=copy.deepcopy(packet['common'])
  for k,value in zip(packet['columns'],row):
   if k in packet['dictionaries']:
    if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<len(packet['dictionaries'][k]):raise ValueError('Invalid evidence dictionary index.')
    value=packet['dictionaries'][k][value]
   record[k]=copy.deepcopy(value)
  if packet['detail_columns'] is not None:
   if len(record['details'])!=len(packet['detail_columns']):raise ValueError('Incomplete evidence details.')
   record['details']={**copy.deepcopy(packet['detail_common']),**dict(zip(packet['detail_columns'],record['details']))}
  for k in packet['shared_detail_fields']:record['details'][k]=copy.deepcopy(record[k])
  result.append(record)
 return result
