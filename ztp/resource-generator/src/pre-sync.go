package main

import (
	"context"
	"fmt"

	apiextensionsclient "k8s.io/apiextensions-apiserver/pkg/client/clientset/clientset"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/rest"
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
		panic(err.Error())
	}
	// creates the clientset
	// clientset, err := kubernetes.NewForConfig(config)
	// if err != nil {
	// 	panic(err.Error())
	// }
	apiextensionsClientSet, err := apiextensionsclient.NewForConfig(config)
	if err != nil {
		panic(err)
	}
	crds, err := apiextensionsClientSet.ApiextensionsV1().CustomResourceDefinitions().List(context.TODO(), metav1.ListOptions{})
	if err != nil {
		panic(err.Error())
	}
	for i := range crds.Items {
		fmt.Println(crds.Items[i].Name, crds.Items[i].Name)
	}
	// cmaps, err := clientset.CoreV1().ConfigMaps("clusters-sub").List(context.TODO(), metav1.ListOptions{})

	// if err != nil {
	// 	panic(err.Error())
	// }
	// fmt.Printf("There are %d config maps in the cluster\n", len(cmaps.Items))
	// for i := range cmaps.Items {
	// 	fmt.Println(cmaps.Items[i].Name, cmaps.Items[i].Data)
	// }

}
