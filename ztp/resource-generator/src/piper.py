#!/usr/bin/python3
import yaml
import fileinput
import os
import tempfile
import time

from watcher import PolicyGenWrapper


def stdin_to_yamls():
    yamls = []
    with fileinput.input() as f:
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
        
            
# Configmap of a name starting with "source-crs" is converted to
#   resources and placed into /tmp/ztp/source-crs
# Other resources:
#   If a Siteconfig / PGT resource has same name+ns as name+label:name 
#   in a Namespace resource, they will be merged to a same file
# Returns: dictionary of groupings

def group_by_gvknns(yamls: list) -> dict:
    grouping = {}
    
    def parse_cm(ym: dict):
        name = ym.get('metadata').get('name')
        if name.startswith("source-crs"):
            os.mkdir("/tmp/ztp/source-crs")
            data = ym.get('data')
            for item in data:
                with open(f"/tmp/ztp/source-crs/{item}", "w") as f:
                    f.write(data[item])
        else:
            parse_other(ym)

    def get_nns(ym: dict):
        name = ym.get('metadata').get('name')
        ns = ym.get('metadata').get('namespace')
        return f"{name}_{ns}"
    
    def group(ym: dict, nns: str):
        if grouping.get(nns) is None:
            grouping[nns] = []
        grouping[nns].append(ym)
    
    def parse_other(ym: dict):
        nns = get_nns(ym)
        group(ym, nns)

    def parse_ns(ym: dict):
        name = ym.get('metadata').get('name')
        ns = ym.get('metadata').get('labels').get('name')
        group(ym, f"{name}_{ns}")



    for item in yamls:
        with open(item, "r") as ym:
            ym_dict = yaml.safe_load(ym)
            os.unlink(item)
            kind = ym_dict.get('kind')
            handle = {
                "ConfigMap": parse_cm,
                "Namespace": parse_ns
            }
            if handle.get(kind) != None:
                handle[kind](ym_dict)
            else:
                parse_other(ym_dict)
    return grouping


def make_files(grouping: dict):
    os.mkdir("/tmp/manifests")
    for item in grouping:
        with open(f"/tmp/manifests/{item}", "w") as f:
            for n in range(len(grouping[item])):
                yaml.dump(grouping[item][n], f, default_flow_style=False)
                f.write("---\n")
        
def find_files(root):
        for d, dirs, files in os.walk(root):
            for f in files:
                yield os.path.join(d, f)


if __name__ == "__main__":
    os.mkdir("/tmp/ztp")
    yamls = stdin_to_yamls()
    grouping = group_by_gvknns(yamls)
    make_files(grouping)
    os.mkdir("/tmp/ztp/pg-out")
    dirs = ("/tmp/manifests", "/tmp/ztp/pg-out")
    PolicyGenWrapper(dirs)

    for item in find_files("/tmp/ztp/pg-out"):
        with open(item, 'r') as f:
            txt = f.read()
            print(txt)
            print("---")
