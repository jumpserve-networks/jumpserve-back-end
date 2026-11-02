"""Owner's standing mandate, separate from currently installed capabilities.

This is trusted controller guidance, never authority supplied by a paper, log,
candidate patch or model. It does not forge an IAM grant or a deployment proof.
"""
from .common import POLICY, canonical, digest

SOURCE_INSTRUCTION = 'I do not want to answer any other questions or be asked to give any more permissions to the supervisor. The supervisor has 100% total authority and control to do whatever it takes to help workers reproduce experiments in academic research papers.'
MANDATE = dict(
    version='paper-reproduction-standing-authority-v1',
    source='Explicit owner instruction in the JumpServe development session',
    source_instruction=SOURCE_INSTRUCTION,
    requires_further_owner_approval=False,
    purpose='Enable workers to reproduce experiments in academic research papers autonomously.',
    repositories=sorted(POLICY['repositories']),
    aws_account='395567831870',supabase_project='regphejnlvfpyokpniny',
    capabilities=[
        'develop-and-deliver-worker-skills-and-tools',
        'install-and-upgrade-dependencies',
        'write-and-repair-experiment-instrumentation',
        'configure-and-provision-isolated-experiment-environments',
        'provision-and-repair-jumpserve-aws-infrastructure-and-service-roles',
        'create-and-migrate-jumpserve-supabase-schema-and-persistence',
        'repair-and-release-worker-and-supervisor-runtimes',
        'run-operational-and-scientific-qualification',
        'merge-and-deploy-qualified-production-changes',
        'verify-production-and-roll-back-failed-releases',
        'diagnose-repair-and-resume-recoverable-assessments',
    ],
    retained_requirements=[
        'honor-owner-pause-and-cancellation',
        'preserve-original-failures-evidence-and-valid-measurements',
        'preserve-frozen-scientific-criteria-and-honest-findings',
        'reconcile-uncertain-actions-before-any-replacement',
        'execute-qualification-and-verify-actual-production-revisions',
        'protect-credentials-and-private-research-data',
        'respect-existing-assessment-deadlines-and-recorded-resource-policy',
    ],
    scope='JumpServe-owned resources and authorized research. This mandate does not confer rights over third-party systems.',
    activation='Standing owner authorization. Installed capabilities and production activation require their own verified technical receipts; never infer them from this mandate.')
MANDATE_SHA256=digest(canonical(MANDATE))


def instruction():
    return ('The owner grants standing authority for autonomous paper reproduction: '+
        ', '.join(MANDATE['capabilities'])+'. Do not ask the owner for routine choices, instructions, tools, skills or additional permission. '+
        'Choose reasonable documented settings within the confirmed research scope. Missing tools and release adapters are implementation work. '+
        'The trusted controller must implement, qualify and execute necessary capability and release paths; the coding sandbox keeps credentials outside its workspace. '+
        'Automatic qualification, exact revision verification, owner cancellation, scientific integrity and uncertain-action reconciliation remain mandatory. '+
        'A missing installed capability is a technical blocker, not missing owner authorization. Record the concrete required implementation and continue other useful work. '+
        'Never claim that a mandate, proposed patch, merge or successful fixture proves an installed capability or scientific completion.')
