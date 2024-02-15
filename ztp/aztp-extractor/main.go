package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"time"

	cu "github.com/openshift-kni/cnf-features-deploy/ztp/pkg/configmap-utils"
	pu "github.com/openshift-kni/cnf-features-deploy/ztp/pkg/policy-utils"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/watch"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	ocmClient "open-cluster-management.io/api/client/cluster/clientset/versioned"
	cluster "open-cluster-management.io/api/cluster/v1"
)

type aztpExtractor struct {
	retryTime     time.Duration
	ocmClientset  *ocmClient.Clientset
	kubeClientSet *kubernetes.Clientset
	ctx           context.Context
	pe            pu.PolicyExtractor
	innerCmName   string
	innerCmNs     string
}

func addNodeConfig(nodeConfig *unstructured.Unstructured) {

	nodeConfig.Object = map[string]interface{}{
		"apiVersion": "config.openshift.io/v1",
		"kind":       "Node",
		"metadata": map[string]interface{}{
			"name": "cluster",
		},
		"spec": map[string]interface{}{
			"cgroupMode": "v1",
		},
	}
}

func getZtpImage() string {
	image := os.Getenv("ZTP_IMAGE")
	if image != "" {
		return image
	}
	return "quay.io/opwnahift-kni/ztp-site-generator:latest"
}

// TODO: pass cluster name instead of mc
func (z *aztpExtractor) convertObjects(mc *cluster.ManagedCluster, variant string) error {
	clusterName := mc.ObjectMeta.Name
	configMapName := fmt.Sprintf("%s-aztp", clusterName)

	err := z.kubeClientSet.CoreV1().ConfigMaps(clusterName).Delete(z.ctx, configMapName, metav1.DeleteOptions{})
	if err != nil && !errors.IsNotFound(err) {
		return err
	}
	policies, err := z.pe.GetPoliciesForNamespace(clusterName)
	if err != nil {
		return err
	}
	objects, err := pu.GetConfigurationObjects(policies)
	if err != nil {
		return err
	}
	log.Printf("found %d policies and %d objects for %s", len(policies), len(objects), clusterName)
	var directlyAppliedObjects []unstructured.Unstructured
	var wrappedObjects []unstructured.Unstructured

	for _, ob := range objects {
		ob.Object["status"] = map[string]interface{}{} // remove status, we can't apply it
		kind := ob.GetKind()
		switch kind {
		case "PerformanceProfile":
			// This is a workaround for the missing cgroup v1 setting when performance profile is applied on day 0
			cgroup := unstructured.Unstructured{}
			addNodeConfig(&cgroup)

			directlyAppliedObjects = append(directlyAppliedObjects, cgroup)
			log.Printf("added %s %s to directlyAppliedObjects", cgroup.GetKind(), cgroup.GetName())
			// This is a workaround for performance profile unable to apply because of a missing webhook
			name := fmt.Sprintf("0001-%s", ob.GetName())
			ob.SetName(name)
			directlyAppliedObjects = append(directlyAppliedObjects, ob)
			log.Printf("added %s %s to directlyAppliedObjects", kind, ob.GetName())
			//"OperatorGroup", "Subscription", - trouble to apply during bootstrap recently

		case "Tuned", "Namespace", "CatalogSource":
			directlyAppliedObjects = append(directlyAppliedObjects, ob)
			log.Printf("added %s %s to directlyAppliedObjects", kind, ob.GetName())
		default:
			wrappedObjects = append(wrappedObjects, ob)
			log.Printf("added %s %s to wrappedObjects", kind, ob.GetName())
		}
	}
	if variant == "full" {
		var data templateData
		data.ZtpImage = getZtpImage()
		objects, err := renderAztpTemplates(data)
		if err != nil {
			return err
		}
		directlyAppliedObjects = append(directlyAppliedObjects, objects...)
	}

	if len(wrappedObjects) > 0 {
		innerCm, err := cu.WrapObjects(wrappedObjects, z.innerCmName, z.innerCmNs)
		if err != nil {
			return err
		}

		innerCmObj, err := runtime.DefaultUnstructuredConverter.ToUnstructured(innerCm)
		if err != nil {
			return err
		}
		var innerCmUns unstructured.Unstructured
		innerCmUns.SetUnstructuredContent(innerCmObj)
		directlyAppliedObjects = append(directlyAppliedObjects, innerCmUns)
	}
	cm, err := cu.WrapObjects(directlyAppliedObjects, configMapName, clusterName)
	if err != nil {
		return err
	}
	_, err = z.kubeClientSet.CoreV1().ConfigMaps(clusterName).Create(z.ctx, cm, metav1.CreateOptions{})
	if err != nil {
		return err
	}
	return nil
}

func (z *aztpExtractor) handleAdd(e watch.Event) error {
	mc := e.Object.(*cluster.ManagedCluster)
	log.Printf("handling addition / modification of managedcluster %s", mc.ObjectMeta.GetName())
	val, found := mc.ObjectMeta.Labels["ztp-accelerated-provisioning"]
	if found && (val == "full" || val == "policies") {
		log.Printf("managedcluster %s is labelled for AZTP variant %s", mc.ObjectMeta.GetName(), val)
		return z.convertObjects(mc, val)
	}
	return nil
}

func (z *aztpExtractor) handleDel(e watch.Event) error {
	mc := e.Object.(*cluster.ManagedCluster)
	clusterName := mc.ObjectMeta.GetName()
	configMapName := fmt.Sprintf("%s-aztp", clusterName)
	log.Printf("handling deletion of managedcluster %s", clusterName)
	err := z.kubeClientSet.CoreV1().ConfigMaps(clusterName).Delete(z.ctx, configMapName, metav1.DeleteOptions{})
	if err != nil && !errors.IsNotFound(err) {
		return err
	}

	return nil
}

func (z *aztpExtractor) watchManagedClusters() error {

	watcher, err := z.ocmClientset.ClusterV1().ManagedClusters().Watch(z.ctx, metav1.ListOptions{})
	if err != nil {
		return err
	}

	for event := range watcher.ResultChan() {
		switch event.Type {
		// case watch.Added:
		case watch.Modified, watch.Added:
			err = z.handleAdd(event)
			if err != nil {
				log.Print(err)
			}
		case watch.Deleted:
			err = z.handleDel(event)
			if err != nil {
				log.Print(err)
			}
		case watch.Error:
			return fmt.Errorf("watcher error: %+v", event)
		}
	}
	return nil
}

func Init() (z *aztpExtractor) {
	z = &aztpExtractor{}
	config, err := rest.InClusterConfig()
	if err != nil {
		log.Fatalln(err)
	}
	z.ocmClientset = ocmClient.NewForConfigOrDie(config)
	z.kubeClientSet = kubernetes.NewForConfigOrDie(config)
	z.pe.PolicyInterface = dynamic.NewForConfigOrDie(config).Resource
	z.pe.Ctx = context.Background()
	z.ctx = z.pe.Ctx

	z.retryTime, err = time.ParseDuration(os.Getenv("RETRY_TIME"))
	if err != nil {
		z.retryTime = 30 * time.Second
	}

	z.innerCmName = os.Getenv("INNER_CONFIGMAP_NAME")
	if z.innerCmName == "" {
		z.innerCmName = "ztp-post-provision"
	}
	z.innerCmNs = os.Getenv("INNER_CONFIGMAP_NAMESPACE")
	if z.innerCmNs == "" {
		z.innerCmNs = "ztp-profile"
	}
	return
}

// main
func main() {

	aztp := Init()
	log.Println("watching ManagedClusters")
	for {
		err := aztp.watchManagedClusters()
		if err != nil {
			log.Printf("managed cluster watcher exited: %s. will retry in %v", err, aztp.retryTime)
			time.Sleep(aztp.retryTime)
		}
	}

}
