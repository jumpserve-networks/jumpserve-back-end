"""Fixed read-only public Context7 MCP integration control. No account data."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from platform_supervisor.documentation import Context7
if __name__=='__main__':
    client=Context7();client.initialize();result=client.query('Next.js','App Router async route params')
    print(json.dumps(dict(passed=bool(result['documentation'].get('content')),library=result['library_id'],authenticated=False,production_writes=0)))
