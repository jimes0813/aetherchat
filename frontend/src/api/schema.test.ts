import { describe, expect, it } from 'vitest';
import type {
  CreateModelBody,
  CreateSessionBody,
  CriticalOperationId,
  HealthPath,
  HealthResponse,
  HealthStatus,
  ListModelsResponse,
  ListSessionsResponse,
  ModelRecord,
  ModelVerifyResult,
  ModelsPath,
  ReadinessPath,
  SessionSummary,
  SessionsPath,
  VerifyModelPath,
  VerifyModelResponse,
} from './types';

describe('openapi generated schema', () => {
  it('exposes critical REST paths at the type level', () => {
    type _SessionsGet = NonNullable<SessionsPath['get']>;
    type _SessionsPost = NonNullable<SessionsPath['post']>;
    type _ModelsGet = NonNullable<ModelsPath['get']>;
    type _ModelsPost = NonNullable<ModelsPath['post']>;
    type _VerifyPost = NonNullable<VerifyModelPath['post']>;
    type _HealthGet = NonNullable<HealthPath['get']>;
    type _ReadyGet = NonNullable<ReadinessPath['get']>;

    const markers: Array<keyof {
      sessionsGet: _SessionsGet;
      sessionsPost: _SessionsPost;
      modelsGet: _ModelsGet;
      modelsPost: _ModelsPost;
      verifyPost: _VerifyPost;
      healthGet: _HealthGet;
      readyGet: _ReadyGet;
    }> = [
      'sessionsGet',
      'sessionsPost',
      'modelsGet',
      'modelsPost',
      'verifyPost',
      'healthGet',
      'readyGet',
    ];

    expect(markers).toHaveLength(7);
  });

  it('keeps request and response aliases assignable for UI usage', () => {
    const createSession = {
      title: 'smoke',
      mode: 'project',
    } as CreateSessionBody;
    const createModel = {
      name: 'kimi',
      type: 'openai_compat',
      model: 'kimi-k3',
      base_url: 'https://api.moonshot.cn/v1',
    } as CreateModelBody;

    const sessions = [
      {
        id: '11111111-1111-4111-8111-111111111111',
        title: 'smoke',
        mode: 'project',
        message_count: 0,
      },
    ] as ListSessionsResponse;
    const models = [
      {
        name: 'kimi',
        type: 'openai_compat',
        model: 'kimi-k3',
        api_key_ref: 'MODEL_KEY_KIMI',
        has_api_key: true,
      },
    ] as ListModelsResponse;
    const verify = {
      ok: true,
      message: '真实对话检测通过',
      sample: 'OK',
      model: 'kimi-k3',
    } as VerifyModelResponse;
    const health = {
      status: 'ok',
      service: 'aetherchat',
      redis: false,
    } as HealthResponse;

    const sessionRow = sessions[0] as SessionSummary;
    const modelRow = models[0] as ModelRecord;
    const verifyRow = verify as ModelVerifyResult;
    const healthRow = health as HealthStatus;

    expect(createSession.title).toBe('smoke');
    expect(createModel.model).toBe('kimi-k3');
    expect(sessionRow.id).toContain('1111');
    expect(modelRow.name).toBe('kimi');
    expect(verifyRow.ok).toBe(true);
    expect(healthRow.service).toBe('aetherchat');
  });

  it('anchors critical operation ids for contract drift detection', () => {
    const ops: CriticalOperationId[] = [
      'list_sessions_sessions__get',
      'create_session_sessions__post',
      'list_models_models__get',
      'create_model_models__post',
      'verify_model_models__name__verify_post',
      'health_health_get',
      'readiness_health_ready_get',
    ];
    expect(new Set(ops).size).toBe(7);
  });
});
