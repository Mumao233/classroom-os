/* Generated from the approved PR-03b identity-control schema. Do not edit. */

/**
 * Contract bundle 0.2.0 candidate. Local broker/D0 and isolated receiver messages, not public OpenAPI administrator routes. JSON validation does not establish authentication, current authority, signatures, time, ACL or transactional facts.
 */
export type ClassroomOSIdentityControlProtocolV1S2ReviewCandidate =
  | D0InstallRecord
  | D0Manifest
  | D0ChangeApproval
  | D0CurrentRequest
  | D0CurrentEvidence
  | D0ChangeRequest
  | D0ChangeResponse
  | CaseOpenRequest
  | CaseOpenResponse
  | ProofChallengeRequest
  | ProofChallengeResponse
  | ProofVerifyRequest
  | ProofVerifyResponse
  | RecoveryIssueRequest
  | RecoveryIssueResponse
  | RecoveryCompleteRequest
  | RecoveryCompleteResponse
  | RecoveryStatusRequest
  | RecoveryStatusResponse
  | ReceiverRegisterRequest
  | ReceiverRegisterResponse
  | ReceiverAwaitRequest
  | ReceiverDelivery
  | ReceiverFactorStartRequest
  | ReceiverFactorStartResponse
  | ReceiverFactorConfirmRequest
  | ReceiverFactorConfirmResponse
  | ReceiverAbandonRequest
  | ReceiverAbandonResponse
  | ControlError
  | ControlHelloRequest
  | ControlHelloResponse
  | ControlAuthenticateRequest
  | ControlAuthenticateResponse;
export type Id = string;
export type Version = number;
export type Hash = string;
export type Nonce = string;
export type Instant = string;
export type SignatureBytes = string;
export type CurrentEvidenceBody = {
  [k: string]: unknown;
} & {
  deployment_id: Id;
  request_nonce: Nonce;
  initialization_complete: true;
  high_water_version: Version;
  high_water_hash: Hash;
  state: "ready" | "transition_reserved";
  transition_id: Id | null;
  issued_at: Instant;
  expires_at: Instant;
};
export type ProofChallengeRequest = {
  [k: string]: unknown;
} & {
  protocol_version: 1;
  kind: "recovery.proof.challenge.request";
  deployment_id: Id;
  case_id: Id;
  op_id: Id;
  action: "issue" | "reissue" | "complete" | "status";
  receiver_id: Id | null;
  expected_flow_generation: Version | null;
};
export type WritePlan = {
  [k: string]: unknown;
} & {
  deployment_id: Id;
  case_id: Id;
  op_id: Id;
  action: WriteAction;
  executor: ExecutorRef;
  authority_version: Version | null;
  target_user_id: Id;
  target_role: "student" | "admin";
  target_security_epoch: Epoch;
  verification_record_hash: Hash;
  receiver_id: Id;
  receiver_key_hash: Hash;
  flow_generation: Version;
  pending_factor_id: Id | null;
  confirmation_hash: Hash | null;
};
export type WriteAction = "issue" | "reissue" | "complete";
export type Epoch = number;
/**
 * Secret transport value; never CLI stdout, URL, environment, log, Receipt or Outbox.
 */
export type Secret = string;
export type ProofVerifyResponse = {
  [k: string]: unknown;
} & {
  protocol_version: 1;
  kind: "recovery.proof.verify.response";
  proof_id: Id;
  case_id: Id;
  op_id: Id;
  action: "issue" | "reissue" | "complete" | "status";
  plan_hash: Hash;
  verified_at: Instant;
  operation_deadline: Instant;
  single_use: boolean;
};
export type RecoveryStatusResponse = {
  [k: string]: unknown;
} & {
  protocol_version: 1;
  kind: "recovery.status.response";
  case_id: Id;
  op_id: Id;
  result: "committed" | "not_committed" | "unknown";
  stage: Stage | null;
  audit_id: Id | null;
  as_of: Instant;
};
export type Stage =
  "unverified_case" | "verified_case" | "pending" | "pending_confirmed" | "completed" | "expired" | "abandoned";
/**
 * Existing student target password verification, not a new-password policy field.
 */
export type Password = string;

export interface D0InstallRecord {
  protocol_version: 1;
  kind: "d0.install_record";
  installation_id: Id;
  deployment_id: Id;
  installer_ref: string;
  resource_authorization_ref: string;
  external_install_approval_ref: string;
  project_user_mapping_ref: string;
  initial_manifest: ManifestBody & {
    version?: 1;
    previous_manifest_hash?: "0000000000000000000000000000000000000000000000000000000000000000";
    [k: string]: unknown;
  };
  manifest_hash: Hash;
  installed_at: Instant;
}
export interface ManifestBody {
  deployment_id: Id;
  version: Version;
  previous_manifest_hash: Hash;
  root_key: Ed25519Key;
  change_key: Ed25519Key;
  maintainer_ref: string;
  authority: AuthorityBinding;
  verifier_version: string;
}
export interface Ed25519Key {
  key_id: Id;
  alg: "Ed25519";
  public_key: Nonce;
}
export interface AuthorityBinding {
  authority_id: Id;
  project_user_mapping_ref: string;
  os_subject: OsSubject;
  recover_key: Ed25519Key;
  enabled: boolean;
}
export interface OsSubject {
  provider: "linux_uid" | "windows_sid";
  subject: string;
}
export interface D0Manifest {
  protocol_version: 1;
  kind: "d0.manifest";
  body: ManifestBody;
  signature: Ed25519Signature;
}
export interface Ed25519Signature {
  alg: "Ed25519";
  key_id: Id;
  signature: SignatureBytes;
}
export interface D0ChangeApproval {
  protocol_version: 1;
  kind: "d0.change_approval";
  body: ChangeBody;
  signature: Ed25519Signature;
}
export interface ChangeBody {
  deployment_id: Id;
  authority_id: Id;
  action: "enroll" | "rotate" | "revoke" | "root_rotate";
  old_manifest_hash: Hash;
  new_manifest_hash: Hash;
  old_version: Version;
  new_version: Version;
  old_binding_hash: Hash;
  new_binding_hash: Hash;
  approval_nonce: Nonce;
  issued_at: Instant;
  expires_at: Instant;
  maintainer_ref: string;
  external_approval_ref: string;
}
export interface D0CurrentRequest {
  protocol_version: 1;
  kind: "d0.current.request";
  deployment_id: Id;
  request_nonce: Nonce;
}
export interface D0CurrentEvidence {
  protocol_version: 1;
  kind: "d0.current.evidence";
  body: CurrentEvidenceBody;
  signature: Ed25519Signature;
}
export interface D0ChangeRequest {
  protocol_version: 1;
  kind: "d0.change.request";
  approval: D0ChangeApproval;
  new_manifest: D0Manifest;
  /**
   * @maxItems 3
   */
  new_key_proofs:
    | []
    | [KeyPossessionProof]
    | [KeyPossessionProof, KeyPossessionProof]
    | [KeyPossessionProof, KeyPossessionProof, KeyPossessionProof];
}
/**
 * Signature covers KeyPopSignedBody under classroom.identity.key-pop.v1, reconstructed with the validated outer approval deployment; not the proof object including signature.
 */
export interface KeyPossessionProof {
  key_purpose: "R0" | "K_change" | "K_recover";
  key_id: Id;
  new_binding_hash: Hash;
  approval_nonce: Nonce;
  signature: Ed25519Signature;
}
export interface D0ChangeResponse {
  protocol_version: 1;
  kind: "d0.change.response";
  deployment_id: Id;
  transition_id: Id;
  version: Version;
  manifest_hash: Hash;
  phase: "reserved" | "pg_committed" | "published_ready";
  audit_id: Id;
}
export interface CaseOpenRequest {
  protocol_version: 1;
  kind: "recovery.case.open.request";
  deployment_id: Id;
  target_user_id: Id;
  recovery_type: "student" | "admin";
  verification_record_ref: string;
}
export interface CaseOpenResponse {
  protocol_version: 1;
  kind: "recovery.case.open.response";
  deployment_id: Id;
  case_id: Id;
  executor: ExecutorRef;
  stage: "unverified_case" | "verified_case";
  verification_record_hash: Hash;
}
export interface ExecutorRef {
  executor_type: "actor" | "authority";
  executor_id: Id;
}
export interface ProofChallengeResponse {
  protocol_version: 1;
  kind: "recovery.proof.challenge.response";
  challenge: ChallengeBody;
}
export interface ChallengeBody {
  challenge_id: Id;
  challenge_nonce: Nonce;
  plan: WritePlan | StatusPlan;
  plan_hash: Hash;
  issued_at: Instant;
  challenge_expiry: Instant;
}
export interface StatusPlan {
  deployment_id: Id;
  case_id: Id;
  op_id: Id;
  action: "status";
  executor: ExecutorRef;
}
export interface ProofVerifyRequest {
  protocol_version: 1;
  kind: "recovery.proof.verify.request";
  challenge_id: Id;
  evidence: AuthorityEvidence | StudentExecutorEvidence;
}
export interface AuthorityEvidence {
  evidence_type: "authority_signature";
  signature: Ed25519Signature;
}
export interface StudentExecutorEvidence {
  evidence_type: "student_admin_reauth";
  reauth_token: Secret;
}
export interface RecoveryIssueRequest {
  protocol_version: 1;
  kind: "recovery.issue.request";
  proof_id: Id;
  case_id: Id;
  op_id: Id;
}
export interface RecoveryIssueResponse {
  protocol_version: 1;
  kind: "recovery.issue.response";
  case_id: Id;
  op_id: Id;
  flow_generation: Version;
  stage: "pending";
  pending_flow_expiry: Instant;
  audit_id: Id;
  result: "committed";
}
export interface RecoveryCompleteRequest {
  protocol_version: 1;
  kind: "recovery.complete.request";
  proof_id: Id;
  case_id: Id;
  op_id: Id;
}
export interface RecoveryCompleteResponse {
  protocol_version: 1;
  kind: "recovery.complete.response";
  case_id: Id;
  op_id: Id;
  stage: "completed";
  result: "committed";
  audit_id: Id;
  full_relogin_required: true;
  recovery_codes_issued: false;
}
export interface RecoveryStatusRequest {
  protocol_version: 1;
  kind: "recovery.status.request";
  proof_id: Id;
  case_id: Id;
  op_id: Id;
}
export interface ReceiverRegisterRequest {
  protocol_version: 1;
  kind: "receiver.register.request";
  public_key: ReceiverJwk;
  client_nonce: Nonce;
}
export interface ReceiverJwk {
  kty: "EC";
  crv: "P-256";
  x: Nonce;
  y: Nonce;
}
export interface ReceiverRegisterResponse {
  protocol_version: 1;
  kind: "receiver.register.response";
  receiver_id: Id;
  receiver_key_hash: Hash;
  server_nonce: Nonce;
  registration_expiry: Instant;
}
export interface ReceiverAwaitRequest {
  protocol_version: 1;
  kind: "receiver.await.request";
  receiver_auth: ReceiverAuthentication;
}
export interface ReceiverAuthentication {
  receiver_id: Id;
  case_id: Id;
  flow_generation: Version;
  server_nonce: Nonce;
  signature: SignatureBytes;
}
export interface ReceiverDelivery {
  protocol_version: 1;
  kind: "receiver.delivery";
  case_id: Id;
  flow_generation: Version;
  cap: Secret;
  pending_flow_expiry: Instant;
  server_nonce: Nonce;
}
export interface ReceiverFactorStartRequest {
  protocol_version: 1;
  kind: "receiver.factor.start.request";
  receiver_auth: ReceiverAuthentication;
  cap: Secret;
  target_password: Password | null;
}
export interface ReceiverFactorStartResponse {
  protocol_version: 1;
  kind: "receiver.factor.start.response";
  case_id: Id;
  flow_generation: Version;
  pending_factor_id: Id;
  totp_seed: string;
  server_nonce: Nonce;
  pending_flow_expiry: Instant;
}
export interface ReceiverFactorConfirmRequest {
  protocol_version: 1;
  kind: "receiver.factor.confirm.request";
  receiver_auth: ReceiverAuthentication;
  cap: Secret;
  pending_factor_id: Id;
  code: string;
}
export interface ReceiverFactorConfirmResponse {
  protocol_version: 1;
  kind: "receiver.factor.confirm.response";
  case_id: Id;
  flow_generation: Version;
  pending_factor_id: Id;
  stage: "pending_confirmed";
  confirmation_hash: Hash;
  server_nonce: Nonce;
}
export interface ReceiverAbandonRequest {
  protocol_version: 1;
  kind: "receiver.abandon.request";
  receiver_auth: ReceiverAuthentication;
  cap: Secret;
}
export interface ReceiverAbandonResponse {
  protocol_version: 1;
  kind: "receiver.abandon.response";
  case_id: Id;
  flow_generation: Version;
  stage: "abandoned";
}
export interface ControlError {
  protocol_version: 1;
  kind: "identity_control.error";
  code:
    "invalid_request" | "denied" | "expired" | "conflict" | "unavailable" | "result_unknown" | "secret_already_issued";
  retryable: boolean;
  correlation_id: Id;
}
export interface ControlHelloRequest {
  protocol_version: 1;
  kind: "identity_control.hello.request";
  deployment_id: Id;
  authentication_mode: "actor" | "authority";
}
export interface ControlHelloResponse {
  protocol_version: 1;
  kind: "identity_control.hello.response";
  deployment_id: Id;
  authentication_challenge_id: Id;
  challenge_nonce: Nonce;
  authentication_mode: "actor" | "authority";
  challenge_expiry: Instant;
}
/**
 * Authority evidence signature covers AuthenticateSignedBody under classroom.identity.authenticate.v1, using the full live hello and kernel-derived OsSubject; actor evidence uses the independent current session verifier.
 */
export interface ControlAuthenticateRequest {
  protocol_version: 1;
  kind: "identity_control.authenticate.request";
  authentication_challenge_id: Id;
  evidence: ActorSessionEvidence | AuthorityEvidence;
}
export interface ActorSessionEvidence {
  evidence_type: "actor_session";
  session_token: Secret;
}
export interface ControlAuthenticateResponse {
  protocol_version: 1;
  kind: "identity_control.authenticate.response";
  auth_context_id: Id;
  executor: ExecutorRef;
  expires_at: Instant;
}
