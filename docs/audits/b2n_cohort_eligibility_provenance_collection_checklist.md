# B2N-P2C Checklist

- Generated: 2026-08-18T14:20:00Z
- Milestone: B2N-P2C — authoritative provenance collection execution checklist
- Decision contract: `docs/audits/b2n_cohort_eligibility_human_review_decision_record_contract.json`

## Frozen cohort binding (exact 20)
- Source manifest: `docs/evidence_platform/oip_v2_2e_2b2u_b2r_frozen_manifest.json`
- Manifest digest: `82bbda32d25a9951a8d8475528d7db3a92b675aae90ce2d55e13391a6b69eedc`
- Members: 20 (ordinals 1..20)

## Required closure fields
- reviewed_migration_origin
- human_approval_decision_record
- immutable_source_checkpoint_id

## Allowed human decisions
- APPROVED
- DENIED
- REQUIRES_REVIEW
- HUMAN_DECISIONS_REQUIRED

## Collection order
1) immutable technical evidence first
2) checkpoint binding second
3) human review last

## Global closure rule
- `COMPLETE = 20`, `PARTIAL = 0`, `MISSING = 0`, `CONFLICTING = 0`
- If any required field is missing for any member: blocker remains `B2N_COHORT_ELIGIBILITY_PROVENANCE_MISSING`

## Per-member status snapshot (initial collection state)

| ordinal | mint | census_event_id | review origin found | checkpoint found | ready for human | human decision | final status |
|---|---|---|---|---|---|---|---|
| 1 | 66ruWUv7KLXkLbHBTnj7PqzEgdue1oZjeA5Z3JMHpump | b223a9f4f6123b98e67c8ba0d8f19d57a7dd1676a9b80b15a8d04a9cfb431aec | False | False | False | False | MISSING |
| 2 | 3hgXvsR8Xh6hnoKyFbPnrMVEkrKGPVbH6EJQkzoNpump | 3e4dbf40558e85f62d9b22151f7e9077a6fd3b31a3946d871a51336de716edb5 | False | False | False | False | MISSING |
| 3 | 5BQpi43RtPxsw7jw3dpeE7duQAXcNYhw9MD6KUxGpump | 21c8cfa392903e39e866598cebf2ae08e0f0b553f4e0485b2bcdc29fb6d5a966 | False | False | False | False | MISSING |
| 4 | 59hy2QNgTB6vvXJqayUk4RgtJ4wCCniwbHpg8Q5gpump | ff681631e3ccf77207381fdebc0a7d5543a3562f26fde09948a3a0a3da78520e | False | False | False | False | MISSING |
| 5 | 6V4rTJ9M4ZNUjvRMvBPiGia5Ju3TutG2nNatRdq1pump | 18cb1a5556e7eea974f5a572a06e82f6c2e9e49f3a2ea35f33f6c0bd18859b6e | False | False | False | False | MISSING |
| 6 | FzKMeRzbfD1wbVf2ZxfdFgYirLLnVwzspt6gJRVTA8WU | 20cce44c2ec91673280a2743f791aafb6090a75bf5cfa7bbf1c851e31b837e0f | False | False | False | False | MISSING |
| 7 | AGfrbmkuqf3UYG5UXEkTpCtw5UMn62Bc9NrAo6rdpump | f154ee9501abac5ad69e7221eea9db0abc2dbf43e22a9223f3a4e6f31f8c90ba | False | False | False | False | MISSING |
| 8 | qgiXEYJD6mXjHqzJJksX1yjYtownjLXFHAtD7KApump | ba42821acc1b64e9f360e962fe403b4a4ff4ca7830c0ae77a99372cd6044e8a0 | False | False | False | False | MISSING |
| 9 | DU8u7EvQsG3GovXDknrcdV8dQfAie7MMY5keqT2Fpump | ce5e2df758814e3d9c610ae806c5942b2adea1ab4234201df1bf9bdff63c4b6f | False | False | False | False | MISSING |
| 10 | Gb71a9p2wfCcuBhHJ2zbG6pchnFGDrocGRAJjZh4pump | 6061d9ed3cb61ae5d40d1703de785e4d3b1bef466c05d0e4a2d7c968441edb37 | False | False | False | False | MISSING |
| 11 | CNCTqxJ2ug2Cg35Z4UwhggmXdfLSTQ4eQgskVdEspump | 8bdfb1de92627f69137070b3f976d1689350ca34af2d9810b368d199ec8c2b47 | False | False | False | False | MISSING |
| 12 | DykmoWpVtaKN9R5vurKk2TWgBX2WvvnbMkJMd7gcpump | 7a9dd1838a2986aee354edb5e44536407c58dcf30abb6e00a6a6e63851043c42 | False | False | False | False | MISSING |
| 13 | E1TTBe4a7qRbLXJ2ef8zUaHE99CqM98oKhCn7YGUpump | b4e81e3b547b31ff09171b3f222929faf69078c4a9d4818d3e5f9d81e3eaa0d4 | False | False | False | False | MISSING |
| 14 | TmzQHAfT2MeuW5mgMP7HK4nZogfrGVxrzYHQBkqpump | 0177a62c18dc35d32b8d116e0a69504a8b3f8d74560bde9d3bc2080d6497e655 | False | False | False | False | MISSING |
| 15 | 2grdcnAX48SivhXjB4WkzKHzsz6b9BMWp1EipZWdpump | ec9e402b7e32441599f9aab258e4818f83e3e51c87be3966d5f8c1e3652c4354 | False | False | False | False | MISSING |
| 16 | D62UbUSyC78Ucdr9jmrCy9veFuuoQEvuL7NYJCNTpump | 050a34d4f50dae7dad3497bb33cab2a15991a4ab28d1b6ee6f2c7f2cc9212500 | False | False | False | False | MISSING |
| 17 | 9S4dmRLq3SmonrBi6ybxV5LW7M2c68NcGfYb6iFepump | 30b7f301b7a3c9424a0f51bcffc0dac9a9c965a3fc2e59b1c11df1b8e10587bd | False | False | False | False | MISSING |
| 18 | CzQDFKURcQHdRgpBib4kyDtmAcWFg9jRQ7Rw3TzGfRGU | 3fafb5cfed47d912330e216f8315021276d465c78e0858cc95eeec9e4c5af283 | False | False | False | False | MISSING |
| 19 | p5HJBxcLPm2xR3jzdNkJtFkY8iqNixmYgevGwzkpump | 7f7103791e68f19cc2722652671ad1560044df72b9a1a7d119dd67fed3dd0e26 | False | False | False | False | MISSING |
| 20 | w9qiqygRmeiYxFFGR6Lf5zcPautE9NDVDX2RG2Lpump | 0ffa96e36e71253aae8d7e05a884426dcd494097b4c87d738bd85c382c15e2bc | False | False | False | False | MISSING |

## Candidate source families (in priority order)
- E.2B2R migration census
- marked migration observation artifacts
- current-mint lineage
- B2M artifacts
- frozen cohort manifests
- source checkpoint / high-water artifacts

## Artifact digest
- `artifact_digest`: `56a940c96d10cd64b46826379021d0cab934ce126e8a5a70362499b538289d02`
