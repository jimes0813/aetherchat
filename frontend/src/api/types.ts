/**
 * Thin typed helpers over the OpenAPI-generated schema.
 * Prefer these aliases when the backend contract is the source of truth.
 */
import type { components, operations, paths } from './schema';

export type { components, operations, paths };

export type Schemas = components['schemas'];

/** Named request/body schemas. */
export type SessionCreate = Schemas['SessionCreate'];
export type ModelCreate = Schemas['ModelCreate'];
export type TaskCreate = Schemas['TaskCreate'];
export type TaskUpdate = Schemas['TaskUpdate'];
export type MemoryPatch = Schemas['MemoryPatch'];
export type ToolCreate = Schemas['ToolCreate'];
export type ToolInvoke = Schemas['ToolInvoke'];
export type HttpValidationError = Schemas['HTTPValidationError'];
export type ValidationError = Schemas['ValidationError'];

/** Named response schemas (Batch 23). */
export type SessionSummary = Schemas['SessionSummary'];
export type SessionDeleted = Schemas['SessionDeleted'];
export type ModelRecord = Schemas['ModelRecord'];
export type ModelVerifyResult = Schemas['ModelVerifyResult'];
export type ModelRuntimeStatus = Schemas['ModelRuntimeStatus'];
export type ModelDeleted = Schemas['ModelDeleted'];
export type HealthStatus = Schemas['HealthStatus'];
export type ReadinessStatus = Schemas['ReadinessStatus'];

/** Path-level presence helpers. */
export type SessionsPath = paths['/sessions/'];
export type ModelsPath = paths['/models/'];
export type HealthPath = paths['/health'];
export type ReadinessPath = paths['/health/ready'];
export type VerifyModelPath = paths['/models/{name}/verify'];

export type CreateSessionBody =
  operations['create_session_sessions__post']['requestBody'] extends {
    content: { 'application/json': infer B };
  }
    ? B
    : never;

export type CreateModelBody =
  operations['create_model_models__post']['requestBody'] extends {
    content: { 'application/json': infer B };
  }
    ? B
    : never;

export type ListSessionsResponse =
  operations['list_sessions_sessions__get']['responses'][200]['content']['application/json'];
export type ListModelsResponse =
  operations['list_models_models__get']['responses'][200]['content']['application/json'];
export type VerifyModelResponse =
  operations['verify_model_models__name__verify_post']['responses'][200]['content']['application/json'];
export type HealthResponse =
  operations['health_health_get']['responses'][200]['content']['application/json'];

/** Operation IDs kept as string-literal anchors for drift detection. */
export type CriticalOperationId =
  | 'list_sessions_sessions__get'
  | 'create_session_sessions__post'
  | 'list_models_models__get'
  | 'create_model_models__post'
  | 'verify_model_models__name__verify_post'
  | 'health_health_get'
  | 'readiness_health_ready_get';
