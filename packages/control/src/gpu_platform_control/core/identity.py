from django.utils.translation import gettext_lazy as tr
import re
from django.core.exceptions import ValidationError

RESERVED = {
    "root",
    "daemon",
    "bin",
    "sys",
    "sync",
    "games",
    "man",
    "lp",
    "mail",
    "news",
    "uucp",
    "proxy",
    "www-data",
    "backup",
    "list",
    "irc",
    "gnats",
    "nobody",
    "_apt",
    "sshd",
    "systemd-network",
    "systemd-resolve",
    "messagebus",
    "polkitd",
}
RESERVED |= {
    "adm",
    "tty",
    "disk",
    "kmem",
    "dialout",
    "fax",
    "voice",
    "cdrom",
    "floppy",
    "tape",
    "sudo",
    "audio",
    "dip",
    "video",
    "plugdev",
    "staff",
    "users",
    "netdev",
    "crontab",
    "ssl-cert",
    "shadow",
    "docker",
}


def validate_login_name(name):
    if not re.fullmatch("[a-z_][a-z0-9_-]{0,31}", name) or name.lower() in RESERVED:
        raise ValidationError(
            tr(
                "Use 1–32 lowercase letters, digits, underscores, or hyphens, beginning with a letter or underscore. System account names are reserved."
            )
        )
    return name
