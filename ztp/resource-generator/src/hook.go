package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"time"

	apiv1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	//
	// Uncomment to load all auth plugins
	// _ "k8s.io/client-go/plugin/pkg/client/auth"
	//
	// Or uncomment to load specific auth plugins
	// _ "k8s.io/client-go/plugin/pkg/client/auth/azure"
	// _ "k8s.io/client-go/plugin/pkg/client/auth/gcp"
	// _ "k8s.io/client-go/plugin/pkg/client/auth/oidc"
	// _ "k8s.io/client-go/plugin/pkg/client/auth/openstack"
)

func main() {
	// creates the in-cluster config
	config, err := rest.InClusterConfig()
	if err != nil {
		kubeconfig := flag.String("kubeconfig", "", "absolute path to the kubeconfig file")
		flag.Parse()

		// use the current context in kubeconfig
		config, err = clientcmd.BuildConfigFromFlags("", *kubeconfig)
		if err != nil {
			panic(err.Error())
		}
	}
	// creates the clientset
	clientset, err := kubernetes.NewForConfig(config)
	if err != nil {
		panic(err.Error())
	}

	err = acquireLock(clientset, "default", "openshift-ztp-status")
	if err != nil {
		fmt.Println(err)
		os.Exit(1)
	}

	fmt.Println("Lock acquired")
}

func acquireLock(clientset *kubernetes.Clientset, ns string, name string) error {
	cmTemplate := &apiv1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: ns,
		},
		Data: map[string]string{
			"lock": "locked",
		},
	}

	for {
		cm, err := clientset.CoreV1().ConfigMaps(ns).Get(context.TODO(), name, metav1.GetOptions{})
		if err != nil {
			if errors.IsNotFound(err) {
				_, err = clientset.CoreV1().ConfigMaps(ns).Create(context.TODO(), cmTemplate, metav1.CreateOptions{})
				if err != nil {
					fmt.Println("Failed to acquire lock: ", err)
					time.Sleep(10 * time.Second)
					continue
				}
				return nil
			}
			fmt.Println("Failed to read status configmap", err)
			return err
		}
		rv := cm.ResourceVersion
		lock, found := cm.Data["lock"]
		if found && lock == "locked" {
			fmt.Println("ZTP resource generator is locked")
			time.Sleep(10 * time.Second)
			continue
		}
		cmTemplate.ObjectMeta.ResourceVersion = rv
		_, err = clientset.CoreV1().ConfigMaps(ns).Update(context.TODO(), cmTemplate, metav1.UpdateOptions{})
		if err != nil {
			if errors.IsConflict(err) {
				fmt.Println("Failed to acquire lock: ", err)
				time.Sleep(10 * time.Second)
				continue
			}
			return err
		}
		return nil
	}
}
