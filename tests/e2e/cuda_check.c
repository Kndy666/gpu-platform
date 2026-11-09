#include <stdio.h>
#include <stdlib.h>
#include <dlfcn.h>
typedef unsigned long long Ptr;
#define FN(name, args) int (*name) args = dlsym(lib,#name); if (!name) {fprintf(stderr,"Missing %s\n",#name);return 1;}
#define CHECK(call) do {int rc=(call);if(rc){fprintf(stderr,"%s failed: %d\n",#call,rc);return 1;}}while(0)
int main(){
 void *lib=dlopen("libcuda.so.1",RTLD_NOW);if(!lib){fprintf(stderr,"%s\n",dlerror());return 1;}
 FN(cuInit,(unsigned));FN(cuDeviceGetCount,(int*));FN(cuDevicePrimaryCtxRetain,(void**,int));FN(cuCtxSetCurrent,(void*));
 FN(cuMemAlloc_v2,(Ptr*,size_t));FN(cuMemsetD32_v2,(Ptr,unsigned,size_t));FN(cuMemcpyDtoH_v2,(void*,Ptr,size_t));FN(cuMemFree_v2,(Ptr));
 CHECK(cuInit(0));int count=0;CHECK(cuDeviceGetCount(&count));if(count!=1){fprintf(stderr,"Unexpected GPU count: %d\n",count);return 1;}
 void *context=NULL;CHECK(cuDevicePrimaryCtxRetain(&context,0));CHECK(cuCtxSetCurrent(context));
 Ptr memory=0;CHECK(cuMemAlloc_v2(&memory,4));CHECK(cuMemsetD32_v2(memory,42,1));unsigned value=0;
 CHECK(cuMemcpyDtoH_v2(&value,memory,4));CHECK(cuMemFree_v2(memory));
 printf("CUDA devices=%d value=%u\n",count,value);return value==42?0:1;
}
