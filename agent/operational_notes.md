# Operational knowledge

## Selecting a tab in Ghostty

A compositor window and a terminal tab are different levels. `niri msg action
focus-window --id ID` focuses the Ghostty **window**, but it does not select a
tab within that window. Processes in several Ghostty tabs can all lead to the
same Ghostty window PID, so finding Claude or Codex in `/proc` is not proof that
its tab is visible.

Ghostty 1.3's default bindings select numbered tabs with `Alt+1` through
`Alt+8` (`Alt+9` selects the last tab). The successful switch to the Claude tab
was to focus the Ghostty window and send `Alt+2`, because Claude was in its
second tab. On this Wayland/Niri desktop, the repeatable form is:

```sh
niri msg action focus-window --id "$window_id"
wtype -s 150 -M alt -k 2 -m alt -s 300
```

Use the actual tab number rather than always using 2. If the number is unknown,
cycle with Ghostty's `Ctrl+Tab` binding and inspect the window title or a
screenshot after each switch. Do not report that a terminal session was focused
until both the containing window and the correct internal tab are visible.
