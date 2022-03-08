#!/usr/bin/python3

import yaml

base_name = "test-sno"
num_sites = 99


with open(f"{base_name}.yaml", "r") as t:
    template = list(yaml.safe_load_all(t))

for site in range(1, num_sites+1):
    print(site)
    name = f"{base_name}-{site}"
    template[0]["metadata"]["name"] = name
    template[0]["metadata"]["labels"]["name"] = name
    template[1]["metadata"]["name"] = name
    template[1]["metadata"]["namespace"] = name
    template[1]["spec"]["clusters"][0]["clusterName"] = name
    template[1]["spec"]["clusters"][0]["clusterLabels"]["sites"] = name
    with open(f"{name}.yaml", "w") as o:
        o.write(yaml.safe_dump_all(template, default_flow_style=False))

