// Private native SIWC refresh. Never emits a credential or accepts model input.
import {DatabaseSync} from 'node:sqlite';
import {pathToFileURL} from 'node:url';
const [databasePath,pluginPath]=process.argv.slice(2);
try {
 const db=new DatabaseSync(databasePath);db.exec('PRAGMA busy_timeout=5000');
 const row=db.prepare("SELECT value_json FROM config_machine_state WHERE state_key='authProfiles.store'").get();
 const value=JSON.parse(row?.value_json??'{}');const profiles=Object.entries(value.profiles??{});
 if(profiles.length!==1)throw new Error('profile-count');
 const [key,credential]=profiles[0];
 if(credential.provider!=='openai'||credential.authFlow!=='chatgpt-token-sharing'||credential.type!=='oauth')throw new Error('subscription-consent');
 if(credential.expires<=Date.now()+90000){
  const {refreshTokenSharingCredential}=await import(pathToFileURL(pluginPath).href);
  value.profiles[key]=await refreshTokenSharingCredential(credential);
  db.prepare("UPDATE config_machine_state SET value_json=? WHERE state_key='authProfiles.store'").run(JSON.stringify(value));
 }
 db.close();process.stdout.write(JSON.stringify({status:'ready'}));
} catch {process.stdout.write(JSON.stringify({status:'auth-required'}));process.exitCode=1;}
