"""Current Supabase category defaults; admission remains server-owned SQL."""
import store

CATEGORIES={'research_assessment','platform_manual','platform_maintenance','platform_repair'}

def snapshot():
    result=store.rpc('autonomy_budget_snapshot',{})
    if not isinstance(result,dict) or result.get('version')!='autonomy-category-budgets-v1' or not isinstance(result.get('categories'),list):
        raise store.StoreError('Current budget policy is unavailable; no default was guessed.')
    rows=result['categories']
    if len(rows)!=len(CATEGORIES) or any(not isinstance(row,dict) or not isinstance(row.get('category'),str) for row in rows) or {row['category'] for row in rows}!=CATEGORIES:
        raise store.StoreError('Current budget category coverage is incomplete.')
    for row in rows:
        if any(type(row.get(key)) is not int or not -1<=row[key]<=2147483647 for key in ('max_spend_cents','max_model_calls')) or type(row.get('revision')) is not int or row['revision']<1:
            raise store.StoreError('Current budget policy has an invalid limit or revision.')
    return {row['category']:row for row in rows}

def assessment_defaults(defaults):
    row=snapshot()['research_assessment']
    return dict(defaults,maximum_spend_usd=None if row['max_spend_cents']==-1 else row['max_spend_cents']/100,
        maximum_model_calls_per_assessment=None if row['max_model_calls']==-1 else row['max_model_calls'],
        budget_policy=terms(row))

def terms(row):
    if row['category']!='research_assessment':raise store.StoreError('Wrong category for assessment budget authority.')
    return dict(version='autonomy-category-budgets-v1',category='research_assessment',revision=row['revision'],
        max_spend_cents=row['max_spend_cents'],max_model_calls=row['max_model_calls'])

def valid_terms(value):
    return (isinstance(value,dict) and set(value)=={'version','category','revision','max_spend_cents','max_model_calls'}
        and value['version']=='autonomy-category-budgets-v1' and value['category']=='research_assessment'
        and type(value['revision']) is int and value['revision']>0
        and all(type(value[k]) is int and -1<=value[k]<=2147483647 for k in ('max_spend_cents','max_model_calls')))
