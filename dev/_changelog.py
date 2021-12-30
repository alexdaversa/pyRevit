"""Create pyRevit change log"""
# pylint: disable=invalid-name,broad-except
import re
import logging
from typing import Dict
from collections import namedtuple, defaultdict

# dev scripts
from scripts import configs
from scripts import utils
from scripts import github

import _props as props


logger = logging.getLogger()


class ChangeAspect:
    """Type representing an aspect of a change (Subsystem, etc.)"""
    DefaultPattern = "- Resolved {ticket}: {title}"

    def __init__(self, label: github.LabelInfo) -> None:
        self._label = label
        self._aspect_type = None
        self._aspect_pattern = ChangeAspect.DefaultPattern
        if m := re.match(r".*\[(.+?)(->(.+?))?\].*", label.description):
            self._aspect_type = m.groups()[0]
            if pattern := m.groups()[2]:
                self._aspect_pattern = f"- {pattern}"

    def __eq__(x, y):
        return isinstance(y, x.__class__) and hash(x) == hash(y)

    def __hash__(self) -> int:
        return hash(self._label.name)

    @property
    def name(self):
        """Log header"""
        return self._label.name

    @property
    def pattern(self):
        """Log message formatting pattern"""
        return self._aspect_pattern


class Change:
    """Type representing a commit point"""

    def __init__(self, commit_hash, message, comments, fetch_info=True):
        self._commit_hash = commit_hash
        self._commit_ticket = Change.find_ticket(message)
        self._commit_todos = Change.find_todos(comments)

        # if ticket number found in message
        # get ticket info from cloud
        self._ticketdata = None
        if fetch_info and self._commit_ticket:
            self._ticketdata = Change.get_ticket_info(self._commit_ticket)

    def __str__(self) -> str:
        message = ChangeAspect.DefaultPattern
        if self.classes:
            default_class = self.classes[0]
            if default_class.pattern:
                message = default_class.pattern

        return message.format(
            ticket=self.ticket,
            url=self.url,
            title=self.title
        )

    @classmethod
    def find_ticket(cls, message):
        """Find ticket number in message"""
        if m := re.match(r".*#(\d+).*", message):
            return m.groups()[0]
        return None

    @classmethod
    def find_todos(cls, comments):
        """Find todo items in comments"""
        todos = []
        for cline in comments.split("\n"):
            if m := re.search(r"\-\s*\[\s*\]\s+(.+)", cline):
                todos.append(m.groups()[0])
        return todos

    @classmethod
    def get_ticket_info(cls, ticket_number):
        """Get ticket data from repository host"""
        return github.get_ticket(ticket_number)

    @property
    def commit_hash(self):
        """Commit hash of the change"""
        return self._commit_hash

    @property
    def ticket(self):
        """Ticket #"""
        return f"#{self._commit_ticket}"

    @property
    def url(self):
        """Ticket url"""
        if self._ticketdata:
            return self._ticketdata.url
        return ""

    @property
    def title(self):
        """Ticket title"""
        if self._ticketdata:
            return self._ticketdata.title
        return ""

    @property
    def subsystems(self):
        """Ticket labels."""
        if self._ticketdata:
            return [
                ChangeAspect(x)
                for x in self._ticketdata.labels
                if "[subsystem" in x.description
            ]
        return []

    @property
    def classes(self):
        """Ticket classes."""
        if self._ticketdata:
            return [
                ChangeAspect(x)
                for x in self._ticketdata.labels
                if "[class" in x.description
            ]
        return []

    @property
    def is_highlighted(self):
        """Is this issue marked as highlighted?"""
        if self._ticketdata:
            return "Highlight" in [x.name for x in self._ticketdata.labels]
        return False

    @property
    def is_priority(self):
        """Is this issue marked as high priority?"""
        if self._ticketdata:
            return "Prioritize" in [x.name for x in self._ticketdata.labels]
        return False


def _find_changes(gitlog_report: str, fetch_info: bool = True):
    """Create changes from git log report"""
    # designed to work with `git log --pretty='format:%h %s%n%b/'`
    changes = []
    idx = 0
    changelines = gitlog_report.split("\n")
    report_length = len(changelines)
    while idx < report_length:
        # extract hash and message
        cline = changelines[idx]
        parts = cline.split(" ", 1)
        if len(parts) != 2:
            idx += 1
            continue
        chash, cmsg = parts
        # print(f"commit -> {chash}: {cmsg}")
        # grab all the comments lines
        idx += 1
        ccmt = ""
        cline = changelines[idx]
        while not cline.startswith("/"):
            ccmt += cline
            idx += 1
            if idx >= report_length:
                break
            cline = changelines[idx]
        # add a new change
        changes.append(
            Change(
                commit_hash=chash,
                message=cmsg,
                comments=ccmt,
                fetch_info=fetch_info,
            )
        )
        idx += 1
    return changes


def _header(text: str, level: int = 2):
    """Print markdown header"""
    print("#" * level + f" {text}")


def report_changelog(args: Dict[str, str]):
    """Report changes from given <tag> to HEAD
    Queries github issue information for better reporting
    """
    target_tag = args["<tag>"]
    if not target_tag:
        # get the latest tag
        latest_tag = utils.system(
            [
                "git",
                "for-each-ref",
                "refs/tags/v*",
                "--sort=-creatordate",
                "--format=%(refname)",
                "--count=1",
            ]
        )
        target_tag = latest_tag.replace("refs/tags/", "")
        args["<tag>"] = target_tag

    # print(f"Target tag is: {target_tag}")

    gitlog_report = utils.system(
        ["git", "log", "--pretty=format:%h %s%n%b%n/", f"{target_tag}..HEAD"]
    )

    # print("Parsing git log for changes...")
    all_changes = _find_changes(gitlog_report, fetch_info=True)

    # groups changes (and purge)
    changes_by_subsystem = defaultdict(list)
    for change in all_changes:
        # print(f"{change.commit_hash} {change.ticket}")
        # skip unintersting commits
        if not change.ticket:
            continue

        for subsystem in change.subsystems:
            changes_by_subsystem[subsystem].append(change)

    # report highlights
    _header("Highlights", level=1)
    for change in all_changes:
        if change.is_highlighted:
            print(change)

    # report changes by groups in order
    _header("Changes", level=1)
    for subsystem, subsystem_changes in changes_by_subsystem.items():
        _header(subsystem.name, level=3)
        for change in subsystem_changes:
            print(change)


def generate_release_notes(args: Dict[str, str]):
    """Generate release notes from given <tag> to HEAD
    Queries github issue information for better reporting
    """
    # print downloads section
    build_version = props.get_version()

    build_version_urlsafe = build_version.replace("+", "%2B")
    base_url = (
        "https://github.com/eirannejad/pyRevit/"
        f"releases/download/v{build_version_urlsafe}/"
    )

    # add easy download links
    print("# Downloads")
    pyrevit_installer = (
        configs.PYREVIT_INSTALLER_NAME.format(version=build_version) + ".exe"
    )
    print(
        "- [pyRevit {version} Installer]({url})".format(
            version=build_version, url=base_url + pyrevit_installer
        )
    )

    pyrevit_admin_installer = (
        configs.PYREVIT_ADMIN_INSTALLER_NAME.format(version=build_version)
        + ".exe"
    )
    print(
        "- [pyRevit {version} Installer - "
        "Admin / All Users / %PROGRAMDATA%]({url})".format(
            version=build_version, url=base_url + pyrevit_admin_installer
        )
    )

    pyrevit_cli_installer = (
        configs.PYREVIT_CLI_INSTALLER_NAME.format(version=build_version)
        + ".exe"
    )
    print(
        "- [pyRevit CLI {version} Installer]({url})".format(
            version=build_version, url=base_url + pyrevit_cli_installer
        )
    )

    pyrevit_cli_admin_installer = (
        configs.PYREVIT_CLI_ADMIN_INSTALLER_NAME.format(version=build_version)
        + ".exe"
    )
    print(
        "- [pyRevit CLI {version} Installer - "
        "Admin / All Users / %PROGRAMDATA%]({url})".format(
            version=build_version, url=base_url + pyrevit_cli_admin_installer
        )
    )

    # output change log
    report_changelog(args)
