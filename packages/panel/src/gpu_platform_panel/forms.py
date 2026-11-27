"""Panel widgets and input coercion; application commands enforce business rules."""

from __future__ import annotations

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as tr, get_language


def label(definition, *, include_unit=True):
    value = definition.get("label_text") or definition.get("label", definition.get("key", ""))
    if isinstance(value, dict):
        value = value.get("zh_hans") if (get_language() or "").lower().startswith("zh") else value.get("en")
        value = value or definition.get("key", "")
    unit = definition.get("unit", "")
    unit = "%" if unit == "percent" else tr("cores") if unit == "cores" else "" if unit == "count" else unit
    return f"{value} ({unit})" if unit and include_unit else str(value)


def choices(records, title="name"):
    return [
        (str(row.get("id", row.get("pk"))), str(row.get(title, row.get("name", row.get("id")))))
        for row in records
    ]


class CompactNumberInput(forms.NumberInput):
    def format_value(self, value):
        from decimal import Decimal, InvalidOperation
        if value is None or value == "":
            return None
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except InvalidOperation:
            return value


class InputForm(forms.Form):
    def __init__(self, data=None, *, options=None, initial=None, **kwargs):
        self.options = options or {}
        super().__init__(data=data, initial={**self.options.get("initial", {}), **(initial or {})}, **kwargs)

    def payload(self):
        return dict(self.cleaned_data)

    def application_error(self, error):
        errors = getattr(error, "message_dict", {"__all__": error.messages})
        for name, messages in errors.items():
            target = name if name in self.fields else None
            for message in messages:
                self.add_error(target, message)


class SSHKeyForm(InputForm):
    name = forms.CharField(label=tr("Key name"), max_length=100)
    public_key = forms.CharField(
        label=tr("SSH keys"), widget=forms.Textarea(attrs={"rows": 3, "placeholder": "ssh-ed25519 AAAA…"})
    )


class TokenIssueForm(InputForm):
    name = forms.CharField(
        label=tr("Name"),
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": tr("For example: laptop, agent")}),
    )
    expires_in = forms.TypedChoiceField(label=tr("Validity"), coerce=int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["expires_in"].choices = self.options.get("lifetimes", [])


class EndTimeForm(InputForm):
    ends_at = forms.DateTimeField(
        label=tr("New end time"),
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )


class ExtensionForm(EndTimeForm):
    reason = forms.CharField(label=tr("Reason"), max_length=2000)


class ReviewForm(InputForm):
    action = forms.ChoiceField(
        choices=[
            ("approve", tr("Approve")),
            ("reject", tr("Reject")),
            ("stop", tr("Stop")),
            ("adjust_time", tr("Adjust time")),
        ]
    )
    reason = forms.CharField(
        label=tr("Reason"), required=False, max_length=2000,
        widget=forms.TextInput(attrs={"required": True}),
    )
    ends_at = forms.DateTimeField(
        label=tr("New end time"),
        required=False,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local", "required": True}
        ),
    )
    priority = forms.TypedChoiceField(
        coerce=int, required=False, choices=[(0, tr("Normal")), (100, tr("High priority"))]
    )


class RequestForm(InputForm):
    node = forms.TypedChoiceField(label=tr("Compute nodes"), coerce=int)
    purpose = forms.CharField(label=tr("Name"), max_length=200)
    image = forms.ChoiceField(label=tr("Environment images"), required=False)
    source_kind = forms.ChoiceField(
        choices=[("image", tr("Images")), ("environment", tr("Configured environment"))],
        required=False,
        initial="image",
    )
    source_environment = forms.ChoiceField(label=tr("Development environments"), required=False)
    command = forms.CharField(label=tr("Command"), required=False, widget=forms.Textarea(attrs={"rows": 4}))
    variables = forms.CharField(
        label=tr("Environment variables"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "OMP_NUM_THREADS=8"}),
    )
    working_dir = forms.CharField(label=tr("Working path"), initial="/workspace")
    starts_at = forms.DateTimeField(
        label=tr("Start time"),
        required=False,
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    ends_at = forms.DateTimeField(
        label=tr("End time"),
        required=False,
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
    )
    notes = forms.CharField(label=tr("Notes"), required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, mode="interactive", **kwargs):
        super().__init__(*args, **kwargs)
        self.mode = mode
        defaults = self.options.get("defaults", {})
        self.initial: dict[str, Any] = {**defaults, **self.initial}
        self.fields["node"].choices = choices(self.options.get("nodes", []))
        self.fields["image"].choices = [("", "—"), *choices(self.options.get("images", []))]
        self.fields["source_environment"].choices = [
            ("", "—"),
            *choices(self.options.get("environments", []), "purpose"),
        ]
        self.fields["starts_at"].required = mode == "interactive"
        self.fields["ends_at"].required = mode == "interactive"
        self.fields["command"].required = mode == "batch"
        self.source_workspaces = self.options.get("source_workspaces", {})
        self.can_source_environment = bool(self.options.get("features", {}).get("save_environment"))
        self.resource_fields: list[Any] = []
        self.volume_fields: list[Any] = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for definition in self.options.get("resources", []):
            if not definition.get("requestable", True):
                continue
            group = definition.get("allocation_group")
            if group:
                grouped.setdefault(group, []).append(definition)
                continue
            kind = definition.get("volume_kind")
            if definition.get("accounting", "task") != "task" and not kind:
                continue
            name = "volume:" + kind if kind else "resource:" + definition["key"]
            initial = defaults.get("volume_sizes" if kind else "resource_quantities", {}).get(
                kind or definition["key"], definition.get("minimum") or 1
            )
            constructor = forms.IntegerField if definition.get("integral") else forms.DecimalField
            self.fields[name] = constructor(
                label=label(definition),
                min_value=definition.get("minimum") or 0,
                max_value=definition.get("maximum"),
                initial=initial, widget=CompactNumberInput,
            )
            self.fields[name].display_label = label(definition, include_unit=False)
            self.fields[name].display_unit = definition.get("unit", "")
            self.fields[name].disabled = kind == "home"
            self.fields[name].widget.attrs.update(
                {"step": definition.get("step") or (1 if definition.get("integral") else "any")}
            )
            if definition.get("quota_available", 0) > 0:
                self.fields[name].widget.attrs["max"] = definition["quota_available"]
            (self.volume_fields if kind else self.resource_fields).append(self[name])
        self.volume_fields = [field for field in self.volume_fields if not field.field.disabled]
        self.custom_resource_fields = self.resource_fields + self.volume_fields
        self.allocation_groups = []
        self.allocation_definitions = grouped
        for group, definitions in grouped.items():
            name = "allocations:" + group
            initial = defaults.get("resource_allocations", {}).get(group, [])
            self.fields[name] = forms.JSONField(required=False, initial=initial, widget=forms.HiddenInput)
            self.allocation_groups.append(
                {"name": group, "label": group.upper(), "field": self[name], "resources": definitions}
            )

    def clean(self):
        values = super().clean()
        if values.get("source_kind") == "environment":
            if not values.get("source_environment"):
                self.add_error("source_environment", tr("Select a development environment."))
        elif not values.get("image"):
            self.add_error("image", tr("Select an image."))
        return values

    def clean_variables(self):
        result = {}
        for row in self.cleaned_data.get("variables", "").splitlines():
            if not row.strip():
                continue
            name, separator, value = row.partition("=")
            if not separator or not name.strip():
                raise forms.ValidationError(tr("Enter one NAME=value pair per line."))
            result[name.strip()] = value.strip()
        return result

    def clean_command(self):
        command = self.cleaned_data.get("command", "").strip()
        return ["/bin/bash", "-c", command] if command else []

    def payload(self):
        values = {key: value for key, value in self.cleaned_data.items() if ":" not in key}
        values.update(mode=self.mode, argv=values.pop("command", []), environment=values.pop("variables", {}))
        source_kind = values.pop("source_kind", "image")
        if source_kind != "environment":
            values["source_environment"] = None
        values["resource_quantities"] = {
            name.removeprefix("resource:"): value
            for name, value in self.cleaned_data.items()
            if name.startswith("resource:")
        }
        values["volume_sizes"] = {
            name.removeprefix("volume:"): value
            for name, value in self.cleaned_data.items()
            if name.startswith("volume:")
        }
        values["resource_allocations"] = {
            name.removeprefix("allocations:"): value or []
            for name, value in self.cleaned_data.items()
            if name.startswith("allocations:")
        }
        return values


class ImageBuildForm(InputForm):
    node = forms.TypedChoiceField(label=tr("Compute nodes"), coerce=int)
    name = forms.SlugField(label=tr("Name"), max_length=80)
    context_git = forms.URLField(label=tr("Git URL"), required=False)
    dockerfile = forms.CharField(label="Dockerfile", widget=forms.Textarea(attrs={"rows": 10}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["node"].choices = choices(self.options.get("nodes", []))


class PolicyForm(InputForm):
    unlimited = forms.BooleanField(label=tr("Unlimited compute resources"), required=False)
    concurrent_tasks = forms.IntegerField(label=tr("Concurrent tasks"), min_value=1, required=False)
    interactive_hours = forms.IntegerField(
        label=tr("Maximum environment duration (hours)"), min_value=1, required=False
    )
    extension_limit = forms.IntegerField(label=tr("Extensions per environment"), min_value=0, initial=1)
    approval_mode = forms.ChoiceField(
        label=tr("Approval policy"),
        choices=[("manual", tr("Manual approval")), ("automatic", tr("Automatic approval"))],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.resource_fields: list[Any] = []
        self.allocation_limit_fields = []
        self.options["resources"] = [row for row in self.options.get("resources", [])
                                     if row.get("accounting") != "volume" or row.get("volume_kind") == "work"]
        for definition in self.options.get("resources", []):
            key = definition["key"]
            name = "resource:" + key
            constructor = forms.IntegerField if key == "system_storage" else forms.DecimalField
            self.fields[name] = constructor(
                label=label(definition),
                min_value=0,
                required=definition.get("accounting") == "volume" or key == "system_storage",
                initial=self.initial.get("resource_limits", {}).get(key), widget=CompactNumberInput,
            )
            if definition.get("accounting") == "image":
                self.fields[name].widget.attrs["placeholder"] = tr("Unlimited")
            self.resource_fields.append(self[name])
            if definition.get("per_allocation"):
                name = "allocation_limit:" + key
                self.fields[name] = forms.DecimalField(
                    label=label(definition),
                    min_value=0,
                    required=False,
                    initial=self.initial.get("per_allocation_limits", {}).get(key), widget=CompactNumberInput,
                )
                self.allocation_limit_fields.append(self[name])
        self.compute_fields = []
        self.storage_fields = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for definition, field in zip(self.options.get("resources", []), self.resource_fields):
            group = definition.get("allocation_group")
            if group:
                grouped.setdefault(group, []).append({
                    "label": label(definition), "total": field,
                    "per_allocation": self["allocation_limit:" + definition["key"]]
                    if definition.get("per_allocation") else None,
                })
            elif definition.get("accounting") in {"volume", "image"} or definition["key"] == "system_storage":
                self.storage_fields.append(field)
            else:
                self.compute_fields.append(field)
        self.allocation_policy_groups = [{"name": key.upper(), "rows": rows} for key, rows in grouped.items()]
        self.custom_resource_fields = self.resource_fields

    def payload(self):
        values = {key: value for key, value in self.cleaned_data.items() if ":" not in key}
        values["resource_limits"] = {
            name.removeprefix("resource:"): value
            for name, value in self.cleaned_data.items()
            if name.startswith("resource:")
        }
        values["per_allocation_limits"] = {
            name.removeprefix("allocation_limit:"): value
            for name, value in self.cleaned_data.items()
            if name.startswith("allocation_limit:")
        }
        return values


class GroupForm(PolicyForm):
    name = forms.CharField(label=tr("Name"), max_length=80)
    home_size_gib = forms.IntegerField(label=tr("Home volume GiB"), min_value=1, initial=50)
    make_default = forms.BooleanField(label=tr("Default for new members"), required=False)


class MemberForm(PolicyForm):
    username = forms.CharField(label=tr("Username"), max_length=32)
    first_name = forms.CharField(label=tr("Full name"), required=False, max_length=150)
    email = forms.EmailField(label=tr("Email"), required=False)
    group = forms.TypedChoiceField(label=tr("User groups"), coerce=int)
    home_size_gib = forms.IntegerField(label=tr("Home volume GiB"), min_value=1)
    password = forms.CharField(
        label=tr("Password"),
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    is_active = forms.BooleanField(label=tr("Enable account"), required=False, initial=True)
    is_staff = forms.BooleanField(label=tr("Administrator"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["group"].choices = choices(self.options.get("groups", []))
        self.group_defaults = {
            key: {
                **values,
                **{"resource:" + name: value for name, value in values.get("resources", {}).items()},
                **{
                    "allocation_limit:" + name: value
                    for name, value in values.get("per_allocation", {}).items()
                },
            }
            for key, values in self.options.get("group_defaults", {}).items()
        }
        self.fields["password"].required = bool(self.options.get("member") is None)
        if not self.options.get("can_grant_admin", False):
            self.fields.pop("is_staff")


class DeleteGroupForm(InputForm):
    destination = forms.TypedChoiceField(label=tr("Move members to"), coerce=int, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["destination"].choices = choices(self.options.get("groups", []))
        self.fields["destination"].required = bool(self.options.get("member_count"))


class NodeForm(InputForm):
    name = forms.SlugField(label=tr("Node name"), max_length=63)
    architecture = forms.ChoiceField(label=tr("Architecture"), choices=[("amd64", "AMD64"), ("arm64", "ARM64")])
    address = forms.CharField(label=tr("Endpoint"), required=False, initial="127.0.0.1")
    transport = forms.ChoiceField(label=tr("Connection module"))
    enabled = forms.BooleanField(label=tr("Enable reporting"), required=False, initial=True)
    maintenance = forms.BooleanField(label=tr("Pause admission"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].disabled = self.options.get("editing", False)
        self.fields["transport"].choices = [(item["name"], item["name"]) for item in self.options.get("modules", [])]
        selected = self.data.get("transport") if self.is_bound else self.initial.get("transport")
        self.connection_fields = []
        schema: dict[str, Any] = next((item["schema"] for item in self.options.get("modules", [])
                       if item["name"] == selected), {})
        connection_labels = {
            "host": tr("SSH host"), "ssh_port": tr("SSH port"), "ssh_user": tr("SSH user"),
            "identity": tr("Private key path"), "key_directory": tr("Key directory"),
            "socket_path": tr("Node socket path"), "proxy_socket": tr("Proxy socket path"),
        }
        for name, specification in schema.get("properties", {}).items():
            key = "transport:" + name
            kind = specification.get("type") or next(
                (item.get("type") for item in specification.get("anyOf", []) if item.get("type") != "null"), "string")
            field_type = forms.IntegerField if kind == "integer" else forms.CharField
            extra = {"min_value": specification.get("minimum"), "max_value": specification.get("maximum")} if kind == "integer" else {}
            self.fields[key] = field_type(
                label=connection_labels.get(name, specification.get("title", name.replace("_", " ").capitalize())),
                required=name in schema.get("required", []), initial=specification.get("default"), **extra)
            self.connection_fields.append(self[key])
        if self.options.get("local"):
            for field in self.fields.values():
                field.disabled = True
            for name in ("enabled", "maintenance"):
                self.fields[name].disabled = False

    def payload(self):
        return {**{key: value for key, value in self.cleaned_data.items() if not key.startswith("transport:")},
                "transport_options": {key.removeprefix("transport:"): value
                                      for key, value in self.cleaned_data.items()
                                      if key.startswith("transport:") and value not in ("", None)}}


class RecycleSettingsForm(InputForm):
    workspace_retention_days = forms.IntegerField(
        label=tr("Work volume retention (days)"), min_value=1, max_value=3650
    )
    snapshot_hours = forms.IntegerField(
        label=tr("Retention after success or cancellation (hours)"), min_value=1, max_value=8760
    )
    failed_snapshot_hours = forms.IntegerField(
        label=tr("Retention after failure (hours)"), min_value=1, max_value=8760
    )


class PushSettingsForm(InputForm):
    enabled = forms.BooleanField(label=tr("Enable notifications"), required=False)
    reminder_minutes = forms.IntegerField(
        label=tr("Reminder before expiry (minutes)"), min_value=1, max_value=1440
    )
    events = forms.MultipleChoiceField(
        label=tr("Notification events"), required=False, widget=forms.CheckboxSelectMultiple
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["events"].choices = self.options.get("events", [])


class ReceiverForm(InputForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, specification in self.options.get("fields", {}).items():
            current = self.options.get("values", {}).get(name)
            secret = specification.get("secret", False)
            field_type = forms.RegexField if specification.get("pattern") else forms.CharField
            constraints = {"regex": specification["pattern"]} if specification.get("pattern") else {}
            self.fields[name] = field_type(
                label=specification.get("label", name), **constraints,
                max_length=4096,
                required=specification.get("required", False) and not current,
                initial=None if secret else current,
                widget=forms.PasswordInput(render_value=False) if secret else forms.TextInput,
            )

    def payload(self):
        return {
            "channel": self.options["channel"],
            "recipient": {key: value for key, value in self.cleaned_data.items() if value},
        }
