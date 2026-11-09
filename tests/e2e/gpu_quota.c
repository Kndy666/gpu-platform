/* CUDA driver-only probe; the default workspace needs no Python or CUDA SDK. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <dlfcn.h>
#include <string.h>
typedef unsigned long long Ptr;
#define FN(name,args) int (*name) args=dlsym(lib,#name); if(!name)return 1;
#define CHECK(call) do {int rc=(call);if(rc){fprintf(stderr,"%s: CUDA %d\n",#call,rc);return 2;}}while(0)
int expected_at(const char *text,int index){
 if(!strchr(text,','))return atoi(text);
 char *copy=strdup(text),*save=NULL,*part=strtok_r(copy,",",&save);int value=-1;
 for(int i=0;part;i++,part=strtok_r(NULL,",",&save))if(i==index){value=atoi(part);break;}
 free(copy);return value;
}
int main(int argc,char **argv){
 if(argc!=6)return 1;
 int expected=atoi(argv[1]),allocation=atoi(argv[4]),hold=atoi(argv[5]);
 void *lib=dlopen("libcuda.so.1",RTLD_NOW);if(!lib)return 1;
 FN(cuInit,(unsigned));FN(cuDeviceGetCount,(int*));FN(cuDeviceGetAttribute,(int*,int,int));
 FN(cuDevicePrimaryCtxRetain,(void**,int));FN(cuCtxSetCurrent,(void*));
 FN(cuMemGetInfo_v2,(size_t*,size_t*));FN(cuMemAlloc_v2,(Ptr*,size_t));
 FN(cuMemsetD32_v2,(Ptr,unsigned,size_t));FN(cuMemcpyDtoH_v2,(void*,Ptr,size_t));
 CHECK(cuInit(0));int count=0;CHECK(cuDeviceGetCount(&count));if(count!=expected)return 3;
 for(int i=0;i<count;i++){
  int sm=expected_at(argv[2],i),limit=expected_at(argv[3],i);
  void *context=NULL;CHECK(cuDevicePrimaryCtxRetain(&context,i));CHECK(cuCtxSetCurrent(context));
  int sms=0;size_t free_=0,total=0;CHECK(cuDeviceGetAttribute(&sms,16,i));CHECK(cuMemGetInfo_v2(&free_,&total));
  printf("GPU %d SM=%d total_MiB=%zu free_MiB=%zu\n",i,sms,total>>20,free_>>20);fflush(stdout);
  if(sms!=sm || (total>>20)!=(size_t)limit)return 4;
  if(allocation){Ptr ptr=0;CHECK(cuMemAlloc_v2(&ptr,(size_t)allocation<<20));CHECK(cuMemsetD32_v2(ptr,42,1));
   unsigned value=0;CHECK(cuMemcpyDtoH_v2(&value,ptr,4));if(value!=42)return 5;}
 }
 printf("READY\n");fflush(stdout);sleep(hold);return 0;
}
