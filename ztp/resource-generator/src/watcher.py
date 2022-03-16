#!/usr/bin/python

import os
import shutil
import sys
import json
import yaml
import tempfile
import subprocess
from kubernetes import client, config
import logging
from jinja2 import Template

mca_delete = """
{
  "apiVersion": "action.open-cluster-management.io/v1beta1",
  "kind": "ManagedClusterAction",
  "metadata": {
      "name": "{{ mca_name }}",
      "namespace": "{{ ns }}"
  },
  "spec": {
      "actionType": "Delete",
        "kube": {
            "resource": "{{ resource }}",
{% if resource_ns %}            
            "namespace": "{{ resource_ns }}",
{% endif %}            
            "name": "{{ name }}"
        }
    }	
}

"""

def find_files(self, root):
    for d, dirs, files in os.walk(root):
        for f in files:
            yield os.path.join(d, f)

class Logger():
    @property
    def logger(self):
        fmt = '%(name)s %(asctime)s [%(levelname)s] \
            [%(module)s:%(lineno)s]: %(message)s'
        name = 'ztp-hooks.watcher'
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            fmt,
            datefmt='%Y-%m-%d %H:%M:%S %Z')

        if not lg.hasHandlers():
            # logging to console
            handler = logging.StreamHandler()
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(formatter)
            lg.addHandler(handler)
        return lg


class ClusterObjApi(Logger):
    def __init__(self, plural):
        try:
            self.api = client.CustomObjectsApi()
            self.group = "ran.openshift.io"
            self.version = "v1"
            self.plural = plural
            self.watch = True
        except Exception as e:
            self.logger.exception(e)

    def watch_resources(self, rv):
        try:
            return self.api.list_cluster_custom_object_with_http_info(
                group=self.group, version=self.version,
                plural=self.plural, watch=self.watch,
                resource_version=rv, timeout_seconds=5)
        except Exception as e:
            self.logger.exception(e)


class PolicyGenWrapper(Logger):
    def __init__(self, paths: list):
        try:
            # Copy the ztp dir to /tmp to allow non-root file creation
            src = '/usr/src/hook/ztp'
            dest = '/tmp/ztp'
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(src, dest)
            cwd = os.path.join(
                '/tmp/ztp/ztp-policy-generator',
                'kustomize/plugin/policyGenerator/v1/policygenerator/')
            command = [
                './PolicyGenerator',
                'dummy_arg',
                paths[0],
                '/tmp/ztp/source-crs',
                paths[1],
                'false']
            env = os.environ.copy()
            env['XDG_CONFIG_HOME'] = cwd
            # Run policy generator
            with subprocess.Popen(
                            command, stderr=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            cwd=cwd, env=env) as pg:
                output = pg.communicate()
                if len(output[1]):
                    raise Exception(f"Manifest conversion failed: {output[1].decode()}")
        except Exception as e:
            self.logger.exception(f"PolicyGenWrapper failed: {e}")
            exit(1)


class OcWrapper(Logger):
    """ wraps the oc cli program for CRUD on a single resource or
    several resources in bulk """
    def __init__(self, action: str):
        self.action = action

    def bulk(self, path: str):
        for f in find_files(path):
            self.file(f)

    def dictionary(self, manifest:dict):
        """ Applies the oc action on a single dictionary manifest """
        fn = tempfile.mktemp()
        with open(fn, "w") as f:
            json.dump(manifest, f)
        self.file(fn)
        os.unlink(fn)

    def file(self, filename: str):
        """ Applies the oc action on a single file specified by path """
        try:
            status = None
            cmd = ["oc", f"{self.action}", "-f", f"{filename}"]
            self.logger.debug(cmd)
            status = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True)
            self.logger.debug(status.stdout.decode())
        except subprocess.CalledProcessError as cpe:
            nl = '\n'
            msg = f"{cpe.stdout.decode()} {cpe.stderr.decode()}"
            with open(filename, 'r') as ef:
                err_file = ef.read()
            self.logger.debug(f"OC wrapper error:{nl}{err_file}")
            self.logger.exception(msg)
            raise Exception(f'Failed to "oc {self.action}" manifest')
        except Exception as e:
            self.logger.exception(e)
            exit(1)

class ApiResponseParser(Logger):
    def __init__(self, api_response, resourcename="siteconfigs", debug=False):
        if api_response[1] != 200:
            raise Exception(f"{resourcename} API call error: {api_response}")
        else:
            try:
                # Create temporary file structure for changed manifests
                self.tmpdir = tempfile.mkdtemp()
                self.del_path = os.path.join(self.tmpdir, 'delete')
                self.del_list = []
                self.upd_path = os.path.join(self.tmpdir, 'update')
                self.upd_list = []
                os.mkdir(self.del_path)
                os.mkdir(self.upd_path)
                self._parse(api_response[0])
                self.logger.debug(f"Objects to delete are: {self.del_list}")
                self.logger.debug(
                    f"Objects to create/update are: {self.upd_list}")

                out_tmpdir = tempfile.mkdtemp()
                out_del_path = os.path.join(out_tmpdir, 'delete')
                out_upd_path = os.path.join(out_tmpdir, 'update')
                os.mkdir(out_del_path)
                os.mkdir(out_upd_path)

                # Do creates / updates
                if len(self.upd_list) > 0:
                    PolicyGenWrapper([self.upd_path, out_upd_path])
                    if resourcename == "siteconfigs":
                        OcWrapper('apply').bulk(out_upd_path)
                    else:
                        self._reconcile_policies(out_upd_path)
                else:
                    self.logger.debug("No objects to update")

                # Do deletes
                if len(self.del_list) > 0:
                    if self._handle_site_deletions():
                        PolicyGenWrapper([self.del_path, out_del_path])
                        OcWrapper('delete').bulk(out_del_path)
                else:
                    self.logger.debug("No objects to delete")

            except Exception as e:
                self.logger.exception(f"Exception by ApiResponseParser: {e}")
                exit(1)
            finally:
                if not debug:
                    shutil.rmtree(self.tmpdir)
                    shutil.rmtree(out_tmpdir)

    def _get_policy_status(self, out_upd_path):
        # Find objects produced by policygen for this sync
        required_objects = []
        for item in find_files(out_upd_path):
            with open(os.path.join(out_upd_path, item), "r") as f:
                opl = list(yaml.safe_load_all(f))
            required_objects.extend(opl)
        # Find PGT namespaces and existing policies
        current_policies = {}
        for item in find_files(self.upd_path):
            with open(os.path.join(out_upd_path, item), "r") as f:
                ipl = list(yaml.safe_load_all(f))
            for pgt in ipl:
                ns = pgt.get("metadata", {}).get("namespace")
                cmd = ["oc", "get", "policy", "-n", f"{ns}", "-o", "json"]
                self.logger.debug(cmd)
                status = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True)
                try:
                    result = json.loads(status.stdout.decode())
                    current_policies[ns] = result
                except Exception as e:
                    self.logger.exception(f"failed to get policies: {e}")
                    exit(1)
        return current_policies, required_objects

    def _reconcile_policies(self, out_upd_path):
        current_policies, required_objects = self._get_policy_status(out_upd_path)
        required_policies = [o for o in required_objects if o.get("kind") == "Policy"]
        for item in required_policies:
            ns = item.get("metadata", {}).get("namespace")
            name = item.get("metadata", {}).get("name")
            
            current = []
            current_policies_items = current_policies.get(ns, {}).get("items", [])
            for i in range(len(current_policies_items)):
                if current_policies_items[i].get("metadata", {}).get("name") == name:
                    current.append(current_policies_items[i])
                    del current_policies[ns]["items"][i]
            if current_policies.get(ns) != None and len(current_policies.get(ns)) == 0:
                del current_policies[ns]
            msg = (f"ns={ns}, name={name}, ",
                   f"current={current}, ",
                   f"required={item}")
            self.logger.debug(msg)
            # apply required and missing objects
            if len(current) == 0:
                ns_required_objects = [o for o in required_objects if o.get(
                    "metadata", {}).get("namespace") == ns]
                for o in ns_required_objects:
                    OcWrapper("apply").dictionary(o)
                    # fn = tempfile.mktemp()
                    # with open(fn, "w") as f:
                    #     json.dump(o, f)
                    # cmd = ["oc", "apply", "-f", f"{fn}"]
                    # status = subprocess.run(
                    #     cmd,
                    #     stdout=subprocess.PIPE,
                    #     stderr=subprocess.PIPE,
                    #     check=True)
                    # self.logger.debug(status.stderr + status.stdout)
                    # os.unlink(fn)
        # Delete remaining existing and not required policies
        for _, v in current_policies.items():
            for it in v.get("items", []):
                OcWrapper("delete").dictionary(it)
                # fn = tempfile.mktemp()
                # with open(fn, "w") as f:
                #     json.dump(it, f)
                # cmd = ["oc", "delete", "-f", f"{fn}"]
                # status = subprocess.run(
                #     cmd,
                #     stdout=subprocess.PIPE,
                #     stderr=subprocess.PIPE,
                #     check=True)
                # self.logger.debug(status.stderr + status.stdout)
                # os.unlink(fn)
                # Now delete the managed cluster objects
                template = Template(mca_delete)
                mng_cluster_objects, clusters = self._extract_enabled_policy_objects(
                    it, "musthave", "enforce")
                for cluster in clusters:
                    ns = cluster.get("clusternamespace")
                    if cluster.get("compliant") == "Compliant":
                        for obj in mng_cluster_objects:
                            name = obj.get("metadata").get("name")
                            resource_ns = obj.get("metadata").get("namespace")
                            resource_name = obj.get("kind").lower()
                            mca_name = f"{ns}.{name}.{resource_name}.delete"
                            manifest = json.loads(template.render(
                                resource=resource_name, resource_ns=resource_ns,
                                ns=ns, name=name, mca_name=mca_name))
                            OcWrapper("create").dictionary(manifest)

                            # manifest = template.render(
                            #     resource=resource_name, resource_ns=resource_ns,
                            #     ns=ns, name=name, mca_name=mca_name)
                            # fn = tempfile.mktemp()
                            # with open(fn, "w") as f:
                            #     f.write(manifest)
                            # cmd = ["oc", "create", "-f", f"{fn}"]
                            # status = subprocess.run(
                            #     cmd,
                            #     stdout=subprocess.PIPE,
                            #     stderr=subprocess.PIPE,
                            #     check=True)
                            # self.logger.debug(status.stderr + status.stdout)
                            # os.unlink(fn)
       
    """ Extract enabled policy objects by filter """
    def _extract_enabled_policy_objects(self,
                                        pol: dict,
                                        compliance_type: str,
                                        remediation_action: str):
        spec = pol.get("spec", {})
        objects = []
        if spec.get("disabled", False) != False:
            return [], []
        spec_ra = spec.get("remediationAction")
        if spec_ra is not None and spec_ra != remediation_action:
            msg = (f"spec remediation action is {spec_ra}, but "
                   f"{remediation_action} was required for policy"
                   f"{pol['metadata']['name']} in "
                   f"{pol['metadata']['namespace']} namespace")
            self.logger.debug(msg)
            return [], []
        policy_templates = spec.get("policy-templates", [])
        for template in policy_templates:
            obj_def = template.get('objectDefinition')
            od_spec = obj_def.get("spec")
            object_templates = od_spec.get('object-templates', [])
            for item in object_templates:
                if item.get("complianceType") == compliance_type and (
                    item.get("remediationAction") is None or 
                    item.get("remediationAction") == remediation_action):
                        objects.append(item.get("objectDefinition"))
        
        return objects, pol.get("status", {}).get("status", [])

    # Note: this solution is limited to SNO (one cluster per siteconfig).
    def _handle_site_deletions(self) -> bool:
        del_siteconfig_list = os.listdir(self.del_path)
        for item in del_siteconfig_list:
            with open(os.path.join(self.del_path, item), "r") as yi:
                obj = yaml.safe_load(yi)
            if obj.get("kind", {}) == "SiteConfig":
                name = obj.get("metadata", {}).get("namespace")
                try:
                    status = None
                    cmd = ["oc", "delete", f"managedcluster/{name}"]
                    self.logger.debug(cmd)
                    status = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True)
                    self.logger.debug(status.stdout.decode())
                    cmd = ["oc", "delete", f"ns/{name}"]
                    self.logger.debug(cmd)
                    status = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True)
                    self.logger.debug(status.stdout.decode())
                    os.unlink(os.path.join(self.del_path, item))
                except Exception as e:
                    self.logger.exception(e)
                    exit(1)
        return len(os.listdir(self.del_path)) > 0

    def _parse(self, resp_data):
        # The response comes in two flavors:
        # 1. For a single object - as a dictionary
        # 2. For several objects - as a text, that must be split to a list
        try:
            if type(resp_data) == str and len(resp_data):
                resp_list = resp_data.split('\n')
                items = (x for x in resp_list if len(x) > 0)
                for item in items:
                    self._create_site_file(json.loads(item))
                    self.logger.debug(item)
            elif type(resp_data) == dict:
                self._create_site_file(resp_data)
                self.logger.debug(resp_data)
            else:
                pass  # Empty response - no changes
        except Exception as e:
            self.logger.Exception(
                f"Exception when parsing API response: {e}")

    def _prune_managed_info(self, site: dict):
        site['object']['metadata'].pop("annotations", None)
        site['object']['metadata'].pop("creationTimestamp", None)
        site['object']['metadata'].pop("managedFields", None)
        site['object']['metadata'].pop("generation", None)
        site['object']['metadata'].pop("resourceVersion", None)
        site['object']['metadata'].pop("selfLink", None)
        site['object']['metadata'].pop("uid", None)

    def _create_site_file(self, site: dict):
        try:
            self._prune_managed_info(site)
            action = site.get("type")
            if action == "DELETED":
                path, lst = self.del_path, self.del_list
            else:
                path, lst = self.upd_path, self.upd_list
            _, name = tempfile.mkstemp(dir=path)
            with open(name, 'w') as f:
                yaml.dump(site.get("object"), f)
            lst.append(site.get("object").get("metadata").get("name"))
        except Exception as e:
            self.logger.exception(e)
            exit(1)


if __name__ == '__main__':
    try:
        lg = Logger()
        config.load_incluster_config()
        lg.logger.debug(f"{sys.argv[1]}, {sys.argv[2]}")
        site_api = ClusterObjApi(sys.argv[2])
        resp = site_api.watch_resources(sys.argv[1])
        debug = len(sys.argv) > 3
        ApiResponseParser(resp, resourcename=sys.argv[2], debug=debug)
    except Exception as e:
        lg.logger.exception(e)
