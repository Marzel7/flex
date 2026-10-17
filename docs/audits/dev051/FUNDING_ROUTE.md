# DEV051 exact-transaction funding audit

Verdict: PARTIAL_WATCHTOWER_ROUTE. Three successful exact Helius lookups, no retries. No further provider requests authorized or performed.

| Stage | Source | Destination | Signature | Amount | Verdict |
|---|---|---|---|---|---|
| Supplied treasury->SubProvider | GzaaMeT8osXc71tFhVZ8pgtDv9dy3nPK7xKMkDZm7zac | CRqEu31bUofuHZVhhSwLFVr9rPHsxx9PEzKRA41uCp1T | UNVERIFIED | UNVERIFIED | NOT_ESTABLISHED: no decoded edge; two additional references not located in checked retained cache/edge authorities; not acquired. |
| Observed treasury funding | GzaaMeT8osXc71tFhVZ8pgtDv9dy3nPK7xKMkDZm7zac | Dd6hgkmaiS5oYx4yU4FFS1khUEDHMnktsBFqgtXm5TzU | 5JoHhi2js4YRMjqD35F8Eo9xJy9swjQkxDfqWnpKJJN25azZEE9q3NH1kdjFVqJem6wY4YHwDmFcubLZP2pVAUo9 | 850.000000000 | VERIFIED System transfer, TX3 outer2 |
| SubProvider fan-out | CRqEu31bUofuHZVhhSwLFVr9rPHsxx9PEzKRA41uCp1T | Multiple distinct Funding Accounts | UNVERIFIED | UNVERIFIED | NOT_VERIFIED. Three transactions contain no such fan-out; retained candidate/payer fan-out checks empty. |
| Selected controller provisioning | Dd6hgkmaiS5oYx4yU4FFS1khUEDHMnktsBFqgtXm5TzU | 7vHXdWsdyDi7byUwndjYeEZKFAAjAhB9yym8EYPwPNqP | 67GxPwV2z4R19UMoPw7isA8cM7V7UwsYz9baUhYLdWivkSgBjkJWXFvzJ7P846DbrDH3dkih7fWjcNh3tiCtXfd7 | 0.253039280 | VERIFIED System createAccountWithSeed, TX2 outer0. SPL-program-owned account initialized to WSOL with Hmc token authority; no native transfer into Hmc wallet proved. |
| Creator/launch-user funding | 7vHXdWsdyDi7byUwndjYeEZKFAAjAhB9yym8EYPwPNqP | 7KX5aXKoU5HtaHnLdgAY21nyKpxEcahxwv9rvExKSxFD | 67GxPwV2z4R19UMoPw7isA8cM7V7UwsYz9baUhYLdWivkSgBjkJWXFvzJ7P846DbrDH3dkih7fWjcNh3tiCtXfd7 | 0.253039280 gross close proceeds | VERIFIED closeAccount TX2 outer2, authorityHmc; creator balance delta confirms entire253039280lamports. Principal/rent split not independently measured. |
| Mint creation/launch user | 7KX5aXKoU5HtaHnLdgAY21nyKpxEcahxwv9rvExKSxFD | BwMMU8aFzdCFyhyFFBp7Ao6RaKnF1iRAbsbCeZBqpump | 3wtMLEFAejTCjqN5koydeFdSSQzppmqqvqbELgRYJtKg7a9q6eT1GwWMUYcnoA7Hbggz1JM4YjirCFTCtaNF7Dxz | Not a provisioning amount | VERIFIED mint account create/initialize/metadata/mintTo CPI sequence under Pump outer0;7KX funds mint creation and signs. Separate Pump creator argument not decoded; canonicalcreator differs. |

TX3:2026-10-17T00:04:32Z,slot457830541. TX2:00:09:28Z,slot457831903. TX1:00:09:30Z,slot457831907. All execution errors null.

Roles: Dd6 supplies nativeSOL and fees; Hmc signs and controls the temporary WSOL account; SPLTokenprogram owns its account data;7KX receives closure proceeds and pays for mintcreation. These are separate identities. No native funding into the Hmc wallet is established. No repeated multi-recipient fan-out is proven.

Canonical intake records a different GENERIC creator and noCREATEsignature; Walkback remainswaitingforCREATEanchor withzeroattempts, no decision/admission/membership. This is not yet a qualifiedWatchtowerfalseNegative. Pump-specific argument bytes were not decoded, so fee-payer/launch-user evidence must not be substituted for that missing semantic proof.

Historical accepted controls shareGza native treasury funding and retainedWSOLclosures but lack complete originalfan-out/selectedaccount proof in the selected retained records. No new numericfan-out threshold or amount-based qualification was invented.

The two additional exact references were checked only in existing retained indexed authorities and were not fetched. Stop at partialevidence; no code, production or canonical changes.
