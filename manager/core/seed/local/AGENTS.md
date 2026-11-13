# Project-specific workflow notes

This file is yours. Bench seeded it because there was none, and no
install, reinstall or update will change it again. Put here what an
agent or teammate needs that bench's own documentation cannot know:
post-merge chores, what the driver assumes, what each local command is
for.

Headless runs are told to read this file as well as the application's
own `AGENTS.md`. Bench does not know how this project is tested, so it
says nothing about that here. Your project's definition of done belongs
in the application's own instructions, or in this file once you write it.

Settings live in `manager/local/.env` (gitignored); every option and its
default is documented in `manager/core/.env.example`. A `checks` file
beside this one replaces `manager/core/checks` as the Focus view's
command classification.
