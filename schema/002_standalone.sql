CREATE TABLE galactica_autonomy.sfera_local_users (
 principal_id uuid PRIMARY KEY REFERENCES public.access_principals(id),
 username text NOT NULL UNIQUE,
 password_hash text NOT NULL,
 role text NOT NULL CHECK(role IN ('reader','contributor','manager','owner')),
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 failed_attempts integer NOT NULL DEFAULT 0,
 locked_until timestamptz
);
CREATE OR REPLACE FUNCTION galactica_autonomy.client_mcp_identity(credential uuid,cookie_hash text)
RETURNS TABLE(credential_id uuid,principal_id uuid,expires_at timestamptz,role text)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT c.id,c.principal_id,c.expires_at,u.role
 FROM public.auth_credentials c
 JOIN galactica_autonomy.sfera_local_users u ON u.principal_id=c.principal_id
 JOIN public.access_principals p ON p.id=c.principal_id
 WHERE c.kind='browser' AND c.revoked_at IS NULL AND c.expires_at>clock_timestamp()
 AND u.status='active' AND p.status='active'
 AND (credential IS NOT NULL OR cookie_hash IS NOT NULL)
 AND (credential IS NULL OR c.id=credential)
 AND (cookie_hash IS NULL OR c.token_hash=cookie_hash)
$$;
CREATE OR REPLACE FUNCTION galactica_autonomy.acl_own_task(actor text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT current_setting('galactica.acl.admin',true)='true' OR EXISTS(
 SELECT 1 FROM galactica_autonomy.sfera_local_users u
 WHERE u.principal_id::text=current_setting('galactica.acl.subject',true)
 AND u.status='active' AND actor='client-mcp:user:'||u.principal_id::text)
$$;
REVOKE ALL ON SCHEMA public,galactica_autonomy,galactica_security FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public,galactica_autonomy,galactica_security FROM PUBLIC;
GRANT USAGE ON SCHEMA public,galactica_autonomy TO sfera_auth,galactica_client_mcp,galactica_ui_acl,galactica_autonomy;
GRANT SELECT,INSERT,UPDATE ON galactica_autonomy.sfera_local_users,public.auth_credentials TO sfera_auth;
GRANT SELECT ON public.access_principals TO sfera_auth;
GRANT SELECT,INSERT,UPDATE ON galactica_autonomy.client_mcp_oauth_clients,galactica_autonomy.client_mcp_authorization_requests,galactica_autonomy.client_mcp_authorization_codes,galactica_autonomy.client_mcp_access_tokens,galactica_autonomy.client_mcp_refresh_tokens TO sfera_auth;
GRANT EXECUTE ON FUNCTION galactica_autonomy.client_mcp_identity(uuid,text) TO sfera_auth,galactica_client_mcp;
GRANT SELECT ON ALL TABLES IN SCHEMA galactica_autonomy TO galactica_client_mcp,galactica_ui_acl;
REVOKE ALL ON galactica_autonomy.sfera_local_users,galactica_autonomy.client_mcp_source_sessions,galactica_autonomy.client_mcp_trend_sessions,galactica_autonomy.client_mcp_access_tokens,galactica_autonomy.client_mcp_refresh_tokens,galactica_autonomy.client_mcp_authorization_codes,galactica_autonomy.client_mcp_authorization_requests FROM galactica_client_mcp,galactica_ui_acl;
GRANT INSERT,UPDATE ON galactica_autonomy.tasks,galactica_autonomy.documents,galactica_autonomy.client_threads,galactica_autonomy.client_thread_messages TO galactica_client_mcp;
GRANT INSERT ON galactica_autonomy.messages,galactica_autonomy.document_versions,galactica_autonomy.change_events TO galactica_client_mcp;
GRANT INSERT ON galactica_autonomy.outcome_protocol_jobs,galactica_autonomy.outcome_link_jobs TO galactica_client_mcp;
GRANT SELECT ON public.access_principals TO galactica_client_mcp;
GRANT SELECT,INSERT,UPDATE,DELETE ON galactica_autonomy.acl_sections,galactica_autonomy.acl_material_sections,galactica_autonomy.acl_groups,galactica_autonomy.acl_group_members,galactica_autonomy.acl_rules TO galactica_ui_acl;
GRANT INSERT ON galactica_autonomy.acl_audit TO galactica_ui_acl;
GRANT UPDATE ON galactica_autonomy.acl_state TO galactica_ui_acl;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA galactica_autonomy TO galactica_client_mcp,galactica_ui_acl,galactica_autonomy;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA galactica_autonomy TO galactica_client_mcp,galactica_ui_acl,galactica_autonomy;
REVOKE EXECUTE ON FUNCTION galactica_autonomy.client_mcp_source_login(text,text,text,text,timestamptz,text),galactica_autonomy.client_mcp_source_activity(uuid,text,text,timestamptz) FROM galactica_client_mcp,galactica_ui_acl,galactica_autonomy;
GRANT ALL ON ALL TABLES IN SCHEMA galactica_autonomy TO galactica_autonomy;
REVOKE ALL ON galactica_autonomy.sfera_local_users FROM galactica_autonomy;
REVOKE EXECUTE ON FUNCTION galactica_autonomy.client_mcp_identity(uuid,text) FROM galactica_ui_acl,galactica_autonomy;
