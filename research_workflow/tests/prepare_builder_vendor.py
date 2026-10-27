"""Make a builder-only vendor copy; retain exact upstream bytes and patch receipt."""
import hashlib,json,os,shutil
from pathlib import Path

def prepare(source,destination,patch_path):
    source=Path(source);destination=Path(destination);patch=json.loads(Path(patch_path).read_bytes())
    original=(source/patch['path']).read_bytes()
    assert hashlib.sha256(original).hexdigest()==patch['original_sha256']
    if not destination.exists():
        destination.parent.mkdir(parents=True,exist_ok=True)
        # Hardlink immutable originals, replace the corrected file's inode below.
        shutil.copytree(source,destination,copy_function=os.link,symlinks=True)
        for dependency in source.parent.iterdir():
            if dependency.name!='openclaw' and not (destination.parent/dependency.name).exists():
                (destination.parent/dependency.name).symlink_to(dependency,target_is_directory=dependency.is_dir())
    target=destination/patch['path'];current=target.read_bytes()
    assert hashlib.sha256(current).hexdigest() in (patch['original_sha256'],patch['corrected_sha256'])
    replacement=original.replace(patch['before'].encode(),patch['after'].encode())
    assert hashlib.sha256(replacement).hexdigest()==patch['corrected_sha256']
    if current!=replacement:
        temporary=target.with_suffix('.builder-correction');temporary.write_bytes(replacement);temporary.replace(target)
    assert (source/patch['path']).read_bytes()==original
    original_path=destination.parent/'builder-patch-original.mjs'
    receipt_path=destination.parent/'builder-patch-receipt.json'
    if original_path.exists():assert original_path.read_bytes()==original
    else:original_path.write_bytes(original)
    receipt=json.dumps(patch,indent=2)+'\n'
    if receipt_path.exists():assert receipt_path.read_text()==receipt
    else:receipt_path.write_text(receipt)
    return destination

if __name__=='__main__':
    root=Path(__file__).resolve().parents[2]
    path=prepare(root/'experiments/openclaw_pilot/runtime/node_modules/openclaw',root/'.test-artifacts/builder-vendor/node_modules/openclaw',root/'research_workflow/openclaw-builder-patch.json')
    print(json.dumps(dict(package=str(path),original_preserved=True)))
