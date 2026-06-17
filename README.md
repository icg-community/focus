# FOCUS
Easy to use project management platform for content creators, with accessibility first design (screen reader and low-vision optimized) and support for essential task types like script writing, voice line submition and overall production progress, including keeping track of the editing stage. Accessible project management for production teams!

## What is FOCUS?

FOCUS is a privacy-first production management hub for content creation teams.

The initial foundation keeps identity separate from public profile data:

* Provider IDs are stored as authentication anchors.
* Public handles are used only as display fallback.
* Users may add an optional display alias.
* New accounts are created without passwords, no passwords can be breached.
* Recovery codes are stored as hashes and become invalid after use.
* Groups must keep at least one owner.

This repository starts with the Django data model and tests so the privacy and
account-continuity rules are explicit before user-facing workflows are added.

