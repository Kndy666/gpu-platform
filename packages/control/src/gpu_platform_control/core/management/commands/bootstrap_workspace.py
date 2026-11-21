import time
from django.core.management.base import BaseCommand, CommandError
from gpu_platform_control.core.nodes.models import ComputeNode, default_node_id
from gpu_platform_control.services.bootstrap import ensure_default_image


class Command(BaseCommand):
    help = "Build and register the configured shared environment image"

    def add_arguments(self, parser):
        nodes = parser.add_mutually_exclusive_group()
        nodes.add_argument("--node", type=int)
        nodes.add_argument("--node-name")
        nodes.add_argument("--all-nodes", action="store_true")
        parser.add_argument("--recipe")
        parser.add_argument("--context")
        parser.add_argument("--wait", type=int, default=1800)
        parser.add_argument("--rebuild", action="store_true")

    def handle(self, *args, **options):
        nodes = ComputeNode.objects.filter(enabled=True)
        if options["node_name"]:
            nodes = nodes.filter(name=options["node_name"])
        elif not options["all_nodes"]:
            nodes = nodes.filter(pk=options.get("node") or default_node_id())
        if not nodes.exists():
            raise CommandError("No configured node matches the selection")
        builds = []
        for node in nodes:
            build = ensure_default_image(
                node, recipe=options["recipe"], context=options["context"], rebuild=options["rebuild"]
            )
            if build:
                builds.append(build)
                self.stdout.write(f"{node.name}: build {build.pk}")
            else:
                self.stdout.write(f"{node.name}: no default image build required")
        if options["wait"] == 0:
            return
        deadline = time.monotonic() + options["wait"]
        while builds and time.monotonic() < deadline:
            for build in list(builds):
                build.refresh_from_db()
                if build.status == "failed":
                    raise CommandError(build.error)
                if build.status == "succeeded":
                    self.stdout.write(f"{build.node.name}: image {build.artifact.reference}")
                    builds.remove(build)
            if builds:
                time.sleep(5)
        if builds:
            raise CommandError("The build is still running; inspect its status in the panel")
