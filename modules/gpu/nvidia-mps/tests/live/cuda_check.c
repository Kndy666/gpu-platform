/* Verify that every requested device can create a context and round-trip device memory. */
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>

typedef unsigned long long DevicePointer;

struct Driver {
    int (*cuInit)(unsigned);
    int (*cuDeviceGetCount)(int *);
    int (*cuDeviceGet)(int *, int);
    int (*cuDevicePrimaryCtxRetain)(void **, int);
    int (*cuDevicePrimaryCtxRelease_v2)(int);
    int (*cuCtxSetCurrent)(void *);
    int (*cuCtxSynchronize)(void);
    int (*cuMemAlloc_v2)(DevicePointer *, size_t);
    int (*cuMemsetD32_v2)(DevicePointer, unsigned, size_t);
    int (*cuMemcpyDtoH_v2)(void *, DevicePointer, size_t);
    int (*cuMemFree_v2)(DevicePointer);
};

static int load_driver(struct Driver *driver, void *library) {
#define LOAD(name) do { \
    driver->name = dlsym(library, #name); \
    if (!driver->name) { fprintf(stderr, "Missing %s\n", #name); return 0; } \
} while (0)
    LOAD(cuInit);
    LOAD(cuDeviceGetCount);
    LOAD(cuDeviceGet);
    LOAD(cuDevicePrimaryCtxRetain);
    LOAD(cuDevicePrimaryCtxRelease_v2);
    LOAD(cuCtxSetCurrent);
    LOAD(cuCtxSynchronize);
    LOAD(cuMemAlloc_v2);
    LOAD(cuMemsetD32_v2);
    LOAD(cuMemcpyDtoH_v2);
    LOAD(cuMemFree_v2);
#undef LOAD
    return 1;
}

static int check(int status, const char *operation, int ordinal) {
    if (status == 0) return 1;
    fprintf(stderr, "CUDA %s failed on device %d (error %d)\n", operation, ordinal, status);
    return 0;
}

static int check_device(const struct Driver *driver, int ordinal) {
    int device;
    void *context = NULL;
    DevicePointer memory = 0;
    unsigned value = 0;
    if (!check(driver->cuDeviceGet(&device, ordinal), "get device", ordinal) ||
        !check(driver->cuDevicePrimaryCtxRetain(&context, device), "retain context", ordinal)) return 0;

    int ok = check(driver->cuCtxSetCurrent(context), "set context", ordinal) &&
             check(driver->cuMemAlloc_v2(&memory, sizeof(value)), "allocate", ordinal) &&
             check(driver->cuMemsetD32_v2(memory, 42, 1), "write", ordinal) &&
             check(driver->cuMemcpyDtoH_v2(&value, memory, sizeof(value)), "read", ordinal);
    if (memory) {
        if (!check(driver->cuCtxSynchronize(), "synchronize", ordinal)) ok = 0;
        if (!check(driver->cuMemFree_v2(memory), "free", ordinal)) ok = 0;
    }
    if (!check(driver->cuCtxSetCurrent(NULL), "clear context", ordinal)) ok = 0;
    if (!check(driver->cuDevicePrimaryCtxRelease_v2(device), "release context", ordinal)) ok = 0;
    if (ok) printf("CUDA device=%d value=%u\n", ordinal, value);
    return ok && value == 42;
}

int main(int argc, char **argv) {
    char *end = NULL;
    long expected = argc == 2 ? strtol(argv[1], &end, 10) : 1;
    if (argc > 2 || (end && *end) || expected < 1 || expected > INT_MAX) return 1;
    void *library = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!library) { fprintf(stderr, "%s\n", dlerror()); return 1; }
    struct Driver driver;
    int count = 0;
    int ok = load_driver(&driver, library) && check(driver.cuInit(0), "initialize", -1) &&
             check(driver.cuDeviceGetCount(&count), "count devices", -1);
    if (ok && count != expected) {
        fprintf(stderr, "Expected %ld CUDA devices, found %d\n", expected, count);
        ok = 0;
    }
    if (ok) {
        for (int ordinal = 0; ordinal < count; ordinal++) {
            if (!check_device(&driver, ordinal)) ok = 0;
        }
    }
    if (ok) printf("CUDA devices=%d value=42\n", count);
    dlclose(library);
    return ok ? 0 : 1;
}
