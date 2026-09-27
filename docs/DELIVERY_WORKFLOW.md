# Delivery workflow

1. Investigate and persist evidence.
2. Generate a deterministic exact patch when the supported binding regression is found.
3. Build a unified diff and file hashes without mutating the workspace.
4. Run safe pre-approval validation and classify deterministic minimum risk.
5. Persist a delivery plan and hash-bound pending approval.
6. Show the diff, base SHA, validation, evidence IDs, and risk in the approval UI.
7. On approval, revalidate plan, hash, action, arguments, and HEAD; atomically acquire the execution lock.
8. Create a branch, apply the exact stored content, verify hashes, validate, commit, push, and create a draft PR.
9. Persist a structured delivery result. Any failed prerequisite stops later mutations.

External GitHub completion requires valid GitHub OAuth credentials and a connected account. It has not been exercised against a live GitHub repository in this workspace.
