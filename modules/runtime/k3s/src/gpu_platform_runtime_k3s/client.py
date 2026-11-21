import json
import subprocess


class Client:
    def __init__(self, namespace="workloads", runner=subprocess.run):
        self.namespace, self.runner = namespace, runner

    def call(self, *args, data=None):
        result = self.runner(
            ["k3s", "kubectl", "-n", self.namespace, *args],
            input=json.dumps(data) if data is not None else None,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

    def get(self, resource, name):
        return self.call("get", resource, name, "--ignore-not-found", "-o", "json")

    def logs(self, name, tail=200):
        result = self.runner(
            [
                "k3s",
                "kubectl",
                "-n",
                self.namespace,
                "logs",
                name,
                "-c",
                "workspace",
                "--tail",
                str(max(1, min(int(tail), 3000))),
                "--limit-bytes",
                "250000",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return result.stdout

    def list(self, resource, selector=None):
        return (self.call("get", resource, "-o", "json", *(["-l", selector] if selector else [])) or {}).get(
            "items", []
        )

    def ensure(self, obj):
        current = self.get(obj["kind"], obj["metadata"]["name"])
        if current:
            self.check_owner(current, obj["metadata"]["annotations"]["platform.task"])
            return current
        try:
            return self.call("create", "-f", "-", "-o", "json", data=obj)
        except subprocess.CalledProcessError:
            current = self.get(obj["kind"], obj["metadata"]["name"])
            if current is None:
                raise
            self.check_owner(current, obj["metadata"]["annotations"]["platform.task"])
            return current

    @staticmethod
    def check_owner(obj, task_id):
        if obj.get("metadata", {}).get("annotations", {}).get("platform.task") != task_id:
            raise PermissionError("runtime.owner_mismatch")

    def delete(self, resource, name, task_id):
        current = self.get(resource, name)
        if current is None:
            return
        self.check_owner(current, task_id)
        # UID preconditions protect a replacement object from a stale deletion.
        path = "/api/v1" if resource != "Job" else "/apis/batch/v1"
        plural = {"Pod": "pods", "Job": "jobs", "Secret": "secrets", "Service": "services"}[resource]
        path += f"/namespaces/{self.namespace}/{plural}/{name}"
        self.call(
            "delete",
            "--raw",
            path,
            "-f",
            "-",
            data={
                "apiVersion": "v1",
                "kind": "DeleteOptions",
                "propagationPolicy": "Foreground",
                "preconditions": {"uid": current["metadata"]["uid"]},
            },
        )
