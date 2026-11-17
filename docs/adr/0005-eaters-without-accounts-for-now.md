# Eaters without accounts for now

**Superseded** by [ADR 0008](0008-accounts-adults-and-teens.md): emu is on the public internet now, so it has accounts.

Eaters are plain records that anyone who can reach the app may add, edit or remove. Tailscale is the only gate, and the app already binds to the Tailscale address alone. We deferred accounts, the Adult, Teen and Child permissions and the rule that children are profiles managed by an adult, because Menus need Eaters, calorie goals and allergies long before they need to know who is logged in. The cost is that anyone on the tailnet can change anyone's allergies, which is acceptable while the tailnet holds only the household's own devices. Add accounts before any device outside the household joins the tailnet or the app is exposed any wider.
