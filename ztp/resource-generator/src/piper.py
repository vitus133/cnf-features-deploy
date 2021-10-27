#!/usr/bin/python3
import yaml
import fileinput
import os
import tempfile
import shutil
from watcher import PolicyGenWrapper


def files_to_yamls(files=None):
    yamls = []
    with fileinput.input(files) as f:
        _, fn = tempfile.mkstemp(dir='/tmp')
        yamls.append(fn)
        inp_dump = open(fn, "w")
        for line in f:
            if not line.startswith('---'):
                inp_dump.write(line)
            else:
                inp_dump.close()
                _, fn = tempfile.mkstemp(dir='/tmp')
                yamls.append(fn)
                inp_dump = open(fn, "w")
    return(yamls)
        

def make_files(yamls: dict):
    os.mkdir("/tmp/manifests")
    os.mkdir("/tmp/ztp/source-crs")

    for item in yamls:
        with open(item, "r") as ym:
            ym_dict = yaml.safe_load(ym)
            if ym_dict.get('kind') == "Configmap" and ym.get('metadata').get('name').startswith("source-crs"):
                data = ym.get('data')
                for item in data:
                    with open(f"/tmp/ztp/source-crs/{item}", "w") as f:
                        f.write(data[item])
                os.unlink(item)
            else:
                shutil.move(f"/tmp/{item}", f"/tmp/manifests/{item}")
        

def find_files(root):
        for d, dirs, files in os.walk(root):
            for f in files:
                yield os.path.join(d, f)


if __name__ == "__main__":
    os.mkdir("/tmp/ztp")
    yamls = files_to_yamls()
    make_files(yamls)
    os.mkdir("/tmp/ztp/pg-out")
    dirs = ("/tmp/manifests", "/tmp/ztp/pg-out")
    PolicyGenWrapper(dirs)

    for item in find_files("/tmp/ztp/pg-out"):
        with open(item, 'r') as f:
            txt = f.read()
            print(txt)
            print("---")
