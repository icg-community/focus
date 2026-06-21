# FOCUS

FOCUS is a privacy-first production management hub for content creation teams. It is being built for blind and sighted creators who need to manage scripts, voice lines, editing work, review stages, and team access without forcing people to disclose private identity details.

The project is in active development. The current app is a Django application with working local workflows, tests, and accessibility-focused templates. It is not production-ready yet.

## Goals

- Keep public profile data separate from sign-in identity.
- Support pseudonymous accounts with optional display names.
- Help production groups manage projects, roles, invitations, assignments, and handoff notes.
- Make account recovery approachable without relying on email addresses or passwords.
- Keep the interface usable with keyboard navigation, screen readers, and low-vision workflows.

## Implemented Functionality

### Account and Sign-In

- Pseudonymous user model with no required email address, legal name, or password.
- Development-only sign-in flow for local work.
- Passkey registration and passkey sign-in.
- One-time backup keys for account recovery.
- Backup key sign-in.
- Account safety page for backup keys, linked accounts, and passkeys.
- Connected account management for development identities.
- Guardrails that prevent users from removing their last remaining access method.
- Safe `next` redirects for sign-in flows.

### Profiles

- Optional display name.
- Optional bio with guidance to avoid private personal details.
- Availability status.
- User-controlled setting to show or hide assigned projects on member profiles.
- Member profile pages scoped to shared production groups.

### Production Groups and Members

- Create production groups.
- Role-based group membership.
- Group owner, admin / producer, editor, talent, and writer roles.
- Member roster with role editing for group owners.
- Owner-only member removal.
- Self-service "leave group" action.
- Protection against removing, demoting, or leaving as the last group owner.
- Access checks so removed or departed members lose access to group content.

### Invitations

- Owner-only invite link creation.
- Invite links assign a selected group role.
- Invite acceptance after sign-in.
- Used invite links cannot be reused.
- Invite revocation.
- Copy invite link button with accessible status messaging and fallback text.

### Projects

- Create and edit video projects inside a production group.
- Track project title, description, status, script URL, asset pipeline URL, assigned editors, and assigned writers.
- Add multiple named project resource links for scripts, asset folders, voice lines, edit timelines, review links, references, and other production materials.
- Status filters on group project lists.
- Assigned project list on the signed-in user's dashboard.
- Assigned projects can be shown on opted-in member profiles.
- Archive and restore projects without deleting notes, assignments, or project history.
- Archived projects are hidden from active project and assignment lists by default.
- Download a Markdown export of a project's summary, links, resources, assignments, and notes.
- Delete projects through a confirmation page when permitted.
- Role-aware project permissions for editing, status updates, notes, resources, archive, restore, and deletion.

### Project Notes

- Add timestamped project notes.
- Status changes automatically create notes.
- Project detail pages show the note history newest first.
- Group project lists, dashboard assignments, and member profile assignments show the latest note.
- Latest-note links go directly to the project notes section.
- Dashboard recent activity shows the latest project note or status update from each project in the user's groups.

### Notifications

- In-app notifications for relevant project status changes, notes, resource changes, archive, and restore actions.
- In-app notifications for invite creation, invite revocation, invite acceptance, role changes, member removal, and members leaving groups.
- Notification inbox filters for all updates, unread updates, project updates, and group updates.
- Site-wide notification polling with temporary visual popups and polite screen reader announcements for new notifications.
- Notification inbox with unread state and a mark-all-read action.
- Notification recipients are scoped to project creators, assigned collaborators, and group owners/admins.

### Accessibility Work So Far

- Semantic Django templates with headings, landmarks, table captions, and scoped table headers.
- Skip link and keyboard-reachable native controls.
- Form fields with labels, help text, `aria-describedby`, error summaries, and invalid state handling.
- Live regions for passkey status, passkey errors, and invite-copy feedback.
- Descriptive link and button text that names the affected project, member, invite, or group.
- Plain-language messages for recovery, passkeys, invitations, and owner-protection rules.

### Public Trust Pages

- About page describing FOCUS and its current development state.
- Plain-language privacy page describing the intended pseudonymous data model.
- Accessibility page describing current accessibility work and testing still needed.
- Status and changelog page with public status signals, a production-readiness checklist, and a health check endpoint.

### Creator Tools

- Public quick speech page with browser voice preview.
- Text edit field and plain text or Markdown file loading for quick speech drafts.
- Optional line splitting so each non-empty line can be prepared as a separate browser speech preview.

## Planned Functionality

The following areas are not complete yet:

- Production-ready authentication provider integrations beyond the local development provider.
- More complete project lifecycle controls, such as richer export formats and retention options.
- More detailed notification preferences.
- Richer project assets and script tracking, including deeper workflow states beyond shared resource links.
- STAR-backed speech generation for downloadable audio files.
- More detailed per-assignment permissions for admins, editors, writers, and talent.
- Stronger production settings, including environment-based secrets, allowed hosts, secure cookies, and deployment documentation.
- Automated accessibility checks in continuous integration.
- Broader manual accessibility testing notes for NVDA, JAWS, Narrator, VoiceOver, keyboard-only use, zoom, and high contrast.
- Contributor documentation for issue triage, pull requests, code review, and release workflow.

## Technology

- Python 3.12 or newer
- Django 5
- SQLite for local development
- WebAuthn passkeys through the `webauthn` Python package

## Local Development Setup

These steps assume you are in the repository root.

### 1. Create a virtual environment

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On macOS or Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

### 2. Install the project

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

### 3. Create the local database

```bash
python manage.py migrate
```

### 4. Run the development server

```bash
python manage.py runserver
```

Open the [local development server](http://127.0.0.1:8000/) in your browser.

In local development, `DEBUG=True` enables the development sign-in page. Use it to create a local pseudonymous session without an email address or password.

## Running Tests

Run the full test suite:

```bash
python manage.py test
```

Run Django's project checks:

```bash
python manage.py check
```

Before sending changes, it is also useful to check for whitespace errors:

```bash
git diff --check
```

## Development Notes

- Keep changes small and tied to a specific user-facing outcome.
- Add or update tests for behavior changes.
- Preserve privacy boundaries. Do not add email, legal name, phone number, or password requirements unless the product direction changes.
- Use native HTML controls before custom controls.
- Keep copy clear and direct, especially for sign-in, recovery, destructive actions, and permissions.
- Do not remove the last account access method for a user.
- Do not remove, demote, or allow leaving when that would leave a group without an owner.

## Accessibility Expectations

FOCUS is intended to follow WCAG 2.2 AA. When changing templates, forms, or frontend behavior:

- Keep one clear page `h1`.
- Use semantic landmarks and native controls.
- Use descriptive link and button text.
- Give data tables captions and scoped headers.
- Associate form help and errors with the relevant field.
- Avoid relying on color alone.
- Make status changes available to assistive technology when content updates without a full page load.
- Test keyboard access and focus order.

## Project Structure

```text
focus_core/
  models.py        Core data model for users, auth identities, groups, projects, resources, notes, invites, and passkeys.
  forms.py         Accessible Django forms and model forms.
  views.py         Sign-in, account safety, groups, members, invites, projects, and notes views.
  urls.py          Application routes.
  tests.py         Django test suite.
  templates/       Server-rendered Django templates.
  static/          CSS and small client-side behavior.
focus_project/
  settings.py      Local Django settings.
  urls.py          Project URL configuration.
```

## License

This project is licensed under the terms in [LICENSE](LICENSE).
