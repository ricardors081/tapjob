# Source Reuse Ledger — TapJob

This ledger becomes mandatory when any upstream code, prompt, fixture, test, SQL or asset is incorporated into TapJob.

## Permission evidence

The project owner states that all 10 upstream owners authorized reuse and that confirmation evidence is retained in e-mail.

Private authorization correspondence must not be committed here. For repositories without a LICENSE file, a reuse PR should state `owner authorization — evidence retained privately` and the date the project owner confirms the authorization.

## Ledger

| TapJob PR | Upstream | Upstream SHA | Source path | Destination | Mode | Permission basis | Tests/evals ported | Security notes |
|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — |

## Mode values

- IMPORT
- ADAPT
- PORT

REFERENCE-only usage does not require a ledger row unless source assets/text are copied.

## Import checklist

- [ ] upstream SHA pinned;
- [ ] source paths listed;
- [ ] permission/license basis recorded;
- [ ] secrets removed;
- [ ] source-specific endpoints/config removed or abstracted;
- [ ] canonical TapJob interface used;
- [ ] tests ported/created;
- [ ] attribution/notices preserved where required;
- [ ] security review complete;
- [ ] no duplicate competing implementation introduced.