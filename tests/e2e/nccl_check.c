/* Multi-process all-reduce through the same NCCL API used by distributed frameworks. */
#include <stdio.h>
#include <stdlib.h>
#include <dlfcn.h>
#include <unistd.h>
#include <sys/wait.h>
typedef struct {char bytes[128];} UniqueId;
#define LOAD(lib,name,args) int (*name) args=dlsym(lib,#name);if(!name)return 1;
#define CHECK(call) do{int rc=(call);if(rc){fprintf(stderr,"%s: %d\n",#call,rc);return 2;}}while(0)
int main(int argc,char **argv){
 void *nccl=dlopen("libnccl.so.2",RTLD_NOW);if(!nccl){fprintf(stderr,"%s\n",dlerror());return 1;}
 LOAD(nccl,ncclGetUniqueId,(UniqueId*));UniqueId id;
 if(argc==2){
  int count=atoi(argv[1]);CHECK(ncclGetUniqueId(&id));
  FILE *file=fopen("id.bin","wb");if(!file)return 1;fwrite(&id,sizeof(id),1,file);fclose(file);
  for(int rank=0;rank<count;rank++)if(!fork()){
   char r[8];snprintf(r,sizeof(r),"%d",rank);execl(argv[0],argv[0],argv[1],r,NULL);_exit(127);
  }
  int status=0,result=0;while(wait(&status)>0)if(!WIFEXITED(status)||WEXITSTATUS(status))result=1;
  return result;
 }
 if(argc!=3)return 1;
 int count=atoi(argv[1]),rank=atoi(argv[2]);FILE *file=fopen("id.bin","rb");if(!file)return 1;
 if(fread(&id,sizeof(id),1,file)!=1)return 1;fclose(file);
 const char *runtime=getenv("PLATFORM_CUDART_LIBRARY");
 void *cuda=dlopen(runtime ? runtime : "libcudart.so",RTLD_NOW);if(!cuda){fprintf(stderr,"%s\n",dlerror());return 1;}
 LOAD(cuda,cudaSetDevice,(int));LOAD(cuda,cudaMalloc,(void**,size_t));LOAD(cuda,cudaMemcpy,(void*,const void*,size_t,int));
 LOAD(cuda,cudaStreamCreate,(void**));LOAD(cuda,cudaStreamSynchronize,(void*));
 LOAD(nccl,ncclCommInitRank,(void**,int,UniqueId,int));LOAD(nccl,ncclAllReduce,(const void*,void*,size_t,int,int,void*,void*));
 LOAD(nccl,ncclCommDestroy,(void*));
 CHECK(cudaSetDevice(rank));void *comm,*data,*stream;int value=rank+1;
 CHECK(cudaMalloc(&data,sizeof(value)));CHECK(cudaMemcpy(data,&value,sizeof(value),1));CHECK(cudaStreamCreate(&stream));
 CHECK(ncclCommInitRank(&comm,count,id,rank));CHECK(ncclAllReduce(data,data,1,2,0,comm,stream));
 CHECK(cudaStreamSynchronize(stream));CHECK(cudaMemcpy(&value,data,sizeof(value),2));CHECK(ncclCommDestroy(comm));
 printf("rank=%d all_reduce=%d expected=%d\n",rank,value,count*(count+1)/2);
 return value==count*(count+1)/2?0:3;
}
