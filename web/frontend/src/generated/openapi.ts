export interface paths {
    "/meta": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getMeta
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getMeta"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/csrf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getCsrf
         * @description Creates/refreshes random preauth-session cookie and bound CSRF token; same-origin no-store; Origin/Fetch-Metadata checked. Authenticated callers obtain token bound to current session. Secret-bearing response is never stored in operation_receipts. Recheck identity, authorization, epochs and expiry before reissuing; never replay old key/cookie/ticket.
         */
        get: operations["getCsrf"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/register": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * register
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["register"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/verify-email": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * verifyEmail
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["verifyEmail"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/resend-verification": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * resendVerification
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["resendVerification"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * login
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics. Secret-bearing response is never stored in operation_receipts. Recheck identity, authorization, epochs and expiry before reissuing; never replay old key/cookie/ticket.
         */
        post: operations["login"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/session": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getSessionContext
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics. Secret-bearing response is never stored in operation_receipts. Recheck identity, authorization, epochs and expiry before reissuing; never replay old key/cookie/ticket.
         */
        get: operations["getSessionContext"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/login/mfa": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * completeLoginMfa
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics. Secret-bearing response is never stored in operation_receipts. Recheck identity, authorization, epochs and expiry before reissuing; never replay old key/cookie/ticket.
         */
        post: operations["completeLoginMfa"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * logout
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["logout"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/logout-all": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * logoutAll
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["logoutAll"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/reauth": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * reauthenticate
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics. Secret-bearing response is never stored in operation_receipts. Recheck identity, authorization, epochs and expiry before reissuing; never replay old key/cookie/ticket.
         */
        post: operations["reauthenticate"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/password/forgot": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * forgotPassword
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["forgotPassword"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/password/reset": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * resetPassword
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["resetPassword"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/password/change": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * changePassword
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["changePassword"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/email/change": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * requestEmailChange
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["requestEmailChange"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/email/confirm": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * confirmEmailChange
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["confirmEmailChange"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/mfa/enroll": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * enrollMfa
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics. Secret-bearing response is never stored in operation_receipts. Recheck identity, authorization, epochs and expiry before reissuing; never replay old key/cookie/ticket.
         */
        post: operations["enrollMfa"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/mfa/confirm": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * confirmMfa
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics. Secret is displayed only in first response, never saved in generic receipts. Lost-response retry returns 409 SECRET_ALREADY_ISSUED; reauthenticate then explicitly rotate/reissue with new operation, invalidating old secret.
         */
        post: operations["confirmMfa"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/mfa/disable": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * disableMfa
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["disableMfa"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/mfa/recovery-codes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * rotateRecoveryCodes
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics. Secret is displayed only in first response, never saved in generic receipts. Lost-response retry returns 409 SECRET_ALREADY_ISSUED; reauthenticate then explicitly rotate/reissue with new operation, invalidating old secret.
         */
        post: operations["rotateRecoveryCodes"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getMe
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getMe"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * updateProfile
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        patch: operations["updateProfile"];
        trace?: never;
    };
    "/me/preferences": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getPreferences
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getPreferences"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * updatePreferences
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        patch: operations["updatePreferences"];
        trace?: never;
    };
    "/me/quota": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getQuota
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getQuota"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me/sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listSessions
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listSessions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me/security-events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listSecurityEvents
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listSecurityEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me/sessions/{session_id}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * revokeSession
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["revokeSession"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me/devices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listDevices
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listDevices"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me/devices/trust": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * trustDevice
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["trustDevice"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me/devices/{device_id}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * revokeDevice
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["revokeDevice"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me/cache-unlock": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * unlockCache
         * @description Online reauthentication and active device/key-epoch checks. TLS no-store response; key held in page memory only. Feature disabled until PR-05a security POC approval. Secret-bearing response is never stored in operation_receipts. Recheck identity, authorization, epochs and expiry before reissuing; never replay old key/cookie/ticket.
         */
        post: operations["unlockCache"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me/deletion-preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * previewAccountDeletion
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["previewAccountDeletion"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/me/delete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * deleteAccount
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["deleteAccount"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/semesters": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listSemesters
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listSemesters"];
        put?: never;
        /**
         * createSemester
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["createSemester"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/semesters/{semester_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getSemester
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getSemester"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * updateSemester
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        patch: operations["updateSemester"];
        trace?: never;
    };
    "/semesters/{semester_id}/deletion-preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * previewSemesterDeletion
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["previewSemesterDeletion"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/semesters/{semester_id}/delete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * deleteSemester
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["deleteSemester"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/courses": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listCourses
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listCourses"];
        put?: never;
        /**
         * createCourse
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["createCourse"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/courses/{course_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getCourse
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getCourse"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * updateCourse
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        patch: operations["updateCourse"];
        trace?: never;
    };
    "/courses/{course_id}/deletion-preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * previewCourseDeletion
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["previewCourseDeletion"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/courses/{course_id}/delete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * deleteCourse
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["deleteCourse"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/courses/{course_id}/materials": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listCourseMaterials
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listCourseMaterials"];
        put?: never;
        /**
         * writeCourseMaterial
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["writeCourseMaterial"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listClassSessions
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listClassSessions"];
        put?: never;
        /**
         * createClassSession
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["createClassSession"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getClassSession
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getClassSession"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * updateClassSession
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        patch: operations["updateClassSession"];
        trace?: never;
    };
    "/class-sessions/{class_session_id}/deletion-preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * previewClassSessionDeletion
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["previewClassSessionDeletion"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/delete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * deleteClassSession
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["deleteClassSession"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/audio-deletion-preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * previewAudioDeletion
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["previewAudioDeletion"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/audio/delete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * deleteAudio
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["deleteAudio"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/capture/acquire": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * acquireCapture
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["acquireCapture"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/capture/renew": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * renewCapture
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["renewCapture"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/capture/pause": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * pauseCapture
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["pauseCapture"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/capture/resume": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * resumeCapture
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["resumeCapture"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/capture/stop": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * stopCapture
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["stopCapture"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/capture/recovery-authorize": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * authorizeRecovery
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["authorizeRecovery"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/audio-manifest": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getAudioManifest
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getAudioManifest"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/audio-chunks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * prepareAudioChunk
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["prepareAudioChunk"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/audio-chunks/{chunk_id}/content": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * putAudioChunk
         * @description Body SHA-256 and sample count must equal prepared immutable descriptor; first persistent object + metadata commit returns durable, retries return same receipt. Retired epoch requires separate recovery authorization; no ASR dispatch implied.
         */
        put: operations["putAudioChunk"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/asr-streams": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * createAsrStream
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["createAsrStream"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/asr-streams/{stream_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getAsrStream
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getAsrStream"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/asr-streams/{stream_id}/finish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * finishAsrStream
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["finishAsrStream"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/transcript-versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listTranscriptVersions
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listTranscriptVersions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/transcript-versions/{version_id}/segments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listTranscriptSegments
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listTranscriptSegments"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/transcript-segments/{segment_id}/corrections": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * correctTranscript
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["correctTranscript"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/transcript-segments/{segment_id}/adopt": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * adoptTranscriptCorrection
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["adoptTranscriptCorrection"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/adopt-transcript": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * adoptTranscriptVersion
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["adoptTranscriptVersion"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/media/uploads": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * createMediaUpload
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["createMediaUpload"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/media/uploads/{upload_id}/content": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * putMediaUpload
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        put: operations["putMediaUpload"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/media/uploads/{upload_id}/complete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * completeMediaUpload
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["completeMediaUpload"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/media/{asset_id}/access": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * issueMediaAccess
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics. Secret-bearing response is never stored in operation_receipts. Recheck identity, authorization, epochs and expiry before reissuing; never replay old key/cookie/ticket.
         */
        post: operations["issueMediaAccess"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/media/{asset_id}/content": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * downloadMedia
         * @description Opaque short-lived ticket + current session + live owner/expiry/tombstone checked on every request and range. Actual response is binary, never a public bucket URL.
         */
        get: operations["downloadMedia"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/slides": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listSlides
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listSlides"];
        put?: never;
        /**
         * createSlide
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["createSlide"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/slides/{slide_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * updateSlide
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        patch: operations["updateSlide"];
        trace?: never;
    };
    "/class-sessions/{class_session_id}/marks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listMarks
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listMarks"];
        put?: never;
        /**
         * createMark
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["createMark"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/marks/{mark_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getMark
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getMark"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * updateMark
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        patch: operations["updateMark"];
        trace?: never;
    };
    "/class-sessions/{class_session_id}/generation-plans": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * prepareGeneration
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["prepareGeneration"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/generation-plans/{plan_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getGenerationPlan
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getGenerationPlan"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/generation-plans/{plan_id}/execute": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * executeGeneration
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["executeGeneration"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/class-sessions/{class_session_id}/automatic-explanation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * setAutomaticExplanation
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["setAutomaticExplanation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tasks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listTasks
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listTasks"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tasks/{task_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getTask
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getTask"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tasks/{task_id}/raw-output": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getRawProviderOutput
         * @description Owner-only bounded raw model output for explicit inspection; render as inert text, no automatic repair call; not provider headers or logs.
         */
        get: operations["getRawProviderOutput"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tasks/{task_id}/reconcile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * reconcileTask
         * @description Read/query existing provider execution when supported. This operation never submits a new paid request; unsupported querying preserves unknown.
         */
        post: operations["reconcileTask"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tasks/{task_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * retryTask
         * @description Creates a distinct attempt/task linkage and reservation. unknown requires explicit duplicate-billing acknowledgement. Existing attempt is immutable; source/consent/expiry checked again.
         */
        post: operations["retryTask"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tasks/{task_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * cancelTask
         * @description Pre-submit may cancel and release. After dispatch returns cancel_requested or unknown until confirmed; never promises vendor cancellation/refund.
         */
        post: operations["cancelTask"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/source-snapshots/{snapshot_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getSourceSnapshot
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getSourceSnapshot"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/questions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listQuestions
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listQuestions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/questions/{question_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * updateQuestion
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        patch: operations["updateQuestion"];
        trace?: never;
    };
    "/review-documents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listReviewDocuments
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listReviewDocuments"];
        put?: never;
        /**
         * createPersonalNote
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["createPersonalNote"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/review-documents/{document_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getReviewDocument
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getReviewDocument"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * updateReviewDocument
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        patch: operations["updateReviewDocument"];
        trace?: never;
    };
    "/review-documents/{document_id}/revisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listReviewRevisions
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listReviewRevisions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/review-documents/{document_id}/personal-revisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * writePersonalRevision
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["writePersonalRevision"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/review-documents/{document_id}/adopt": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * adoptReviewRevision
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["adoptReviewRevision"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/sync/operations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * syncOperations
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["syncOperations"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/sync/changes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getChanges
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getChanges"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/sync/bootstrap": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * bootstrapSync
         * @description Bounded metadata snapshot + committed owner event clock in the same REPEATABLE READ transaction. No body text. Return CAPACITY_EXCEEDED instead of truncation. Retain unsynced drafts separately; hydrate content by exact revision, then replay changes after watermark.
         */
        get: operations["bootstrapSync"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/operations/{op_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getOperationReceipt
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getOperationReceipt"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/sync/conflicts/{conflict_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getConflict
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getConflict"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/sync/conflicts/{conflict_id}/resolve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * resolveConflict
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["resolveConflict"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/exports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * createExport
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["createExport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/imports/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * previewImport
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["previewImport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/imports/commit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * commitImport
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["commitImport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/deletions/{deletion_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getDeletion
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getDeletion"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/search": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * search
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["search"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/notifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * listNotifications
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["listNotifications"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/notifications/{notification_id}/read": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * readNotification
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["readNotification"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/feedback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * submitFeedback
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["submitFeedback"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/users": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * adminListUsers
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["adminListUsers"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/users/{user_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * adminUpdateUser
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        patch: operations["adminUpdateUser"];
        trace?: never;
    };
    "/admin/users/{user_id}/quota": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * adminGetUserQuota
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["adminGetUserQuota"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/invitations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * adminListInvitations
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["adminListInvitations"];
        put?: never;
        /**
         * adminCreateInvitation
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics. Secret is displayed only in first response, never saved in generic receipts. Lost-response retry returns 409 SECRET_ALREADY_ISSUED; reauthenticate then explicitly rotate/reissue with new operation, invalidating old secret.
         */
        post: operations["adminCreateInvitation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/invitations/{invitation_id}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * adminRevokeInvitation
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["adminRevokeInvitation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/invitations/{invitation_id}/reissue": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * adminReissueInvitation
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics. Secret is displayed only in first response, never saved in generic receipts. Lost-response retry returns 409 SECRET_ALREADY_ISSUED; reauthenticate then explicitly rotate/reissue with new operation, invalidating old secret.
         */
        post: operations["adminReissueInvitation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/quota-adjustments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * adminAdjustQuota
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        post: operations["adminAdjustQuota"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/usage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * adminGetUsage
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["adminGetUsage"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * adminGetHealth
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["adminGetHealth"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/switches": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * adminGetSwitches
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["adminGetSwitches"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * adminUpdateSwitches
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        patch: operations["adminUpdateSwitches"];
        trace?: never;
    };
    "/admin/audit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * adminListAudit
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["adminListAudit"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/ws-tickets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * createWsTicket
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics. Secret-bearing response is never stored in operation_receipts. Recheck identity, authorization, epochs and expiry before reissuing; never replay old key/cookie/ticket.
         */
        post: operations["createWsTicket"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/review-revisions/{revision_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * getReviewRevision
         * @description Resource ownership, tombstones and active account checked on the server. See 04-CONTRACTS-FLOWS.md for transaction semantics.
         */
        get: operations["getReviewRevision"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        Empty: Record<string, never>;
        Acknowledged: {
            accepted: boolean;
            /** Format: uuid */
            request_id: string;
        };
        Error: {
            error: {
                /** @enum {string} */
                code: "UNAUTHENTICATED" | "FORBIDDEN" | "NOT_FOUND" | "GONE" | "VALIDATION_FAILED" | "RATE_LIMITED" | "REVISION_CONFLICT" | "IDEMPOTENCY_MISMATCH" | "LEASE_CONFLICT" | "STALE_FENCE" | "SOURCE_CHANGED" | "SOURCE_UNAVAILABLE" | "CONSENT_REQUIRED" | "PLAN_EXPIRED" | "QUOTA_EXCEEDED" | "BUDGET_BLOCKED" | "SERVICE_DISABLED" | "RESULT_UNKNOWN" | "OUTPUT_INVALID" | "STORAGE_UNAVAILABLE" | "CAPACITY_EXCEEDED" | "CURSOR_EXPIRED" | "INCOMPATIBLE_CLIENT" | "MFA_REQUIRED" | "REAUTH_REQUIRED" | "INVITATION_UNAVAILABLE" | "SECRET_ALREADY_ISSUED";
                message: string;
                /** Format: uuid */
                request_id: string;
                retryable: boolean;
                retry_after_seconds: number | null;
                current_revision: number | null;
                conflict_id: string | null;
                task_id: string | null;
            };
        };
        Meta: {
            product_version: string;
            /** @constant */
            api_version: "web/v1";
            schema_revision: string;
            minimum_client_build: string;
            /** @constant */
            contract_version: "0.1.0";
            /** Format: date-time */
            server_time: string;
            features: {
                registration: boolean;
                asr: boolean;
                ai: boolean;
                persistent_recovery: boolean;
                trusted_cache: boolean;
            };
            limits: {
                max_upload_bytes: number;
                max_archive_entries: number;
                max_audio_chunk_bytes: number;
                capture_lease_seconds: number;
                heartbeat_seconds: number;
            };
        };
        RegisterRequest: {
            /** Format: email */
            email: string;
            password: string;
            invitation_token: string;
            nickname?: string;
        };
        EmailRequest: {
            /** Format: email */
            email: string;
        };
        TokenRequest: {
            token: string;
        };
        CsrfBootstrap: {
            csrf_token: string;
            /** Format: date-time */
            expires_at: string;
        };
        LoginRequest: {
            /** Format: email */
            email: string;
            password: string;
            remember_me: boolean;
            device_name: string;
        };
        LoginResult: {
            /** @enum {string} */
            state: "authenticated" | "mfa_required";
            challenge_id: string | null;
            user: components["schemas"]["User"] | null;
            device_id: string | null;
            csrf_token: string | null;
        };
        SessionContext: {
            user: components["schemas"]["User"];
            /** Format: uuid */
            device_id: string;
            /** Format: uuid */
            session_id: string;
            csrf_token: string;
        };
        MfaChallengeRequest: {
            /** Format: uuid */
            challenge_id: string;
            code: string;
        };
        ReauthRequest: {
            password: string;
            mfa_code?: string;
        };
        ReauthResult: {
            reauth_token: string;
            /** Format: date-time */
            expires_at: string;
        };
        PasswordResetRequest: {
            token: string;
            new_password: string;
        };
        PasswordChangeRequest: {
            new_password: string;
        };
        EmailChangeRequest: {
            /** Format: email */
            new_email: string;
        };
        MfaEnrollment: {
            /** Format: uuid */
            enrollment_id: string;
            otpauth_uri: string;
            /** Format: date-time */
            expires_at: string;
        };
        MfaEnrollmentConfirm: {
            /** Format: uuid */
            enrollment_id: string;
            code: string;
        };
        RecoveryCodes: {
            codes: string[];
            /** @constant */
            shown_once: true;
        };
        User: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: email */
            email: string;
            nickname: string;
            school: string;
            major: string;
            /** @enum {string} */
            role: "student" | "admin";
            /** @enum {string} */
            status: "pending" | "active" | "disabled" | "deleting";
            mfa_enabled: boolean;
        };
        ProfilePatch: {
            base_revision: number;
            nickname?: string;
            school?: string;
            major?: string;
        };
        Preferences: {
            revision: number;
            /** @enum {string} */
            theme: "system" | "day" | "night";
            reduced_effects: boolean;
            reading_font_px: number;
            /** @enum {string} */
            explanation_language: "auto" | "zh" | "en";
            automatic_explanation: boolean;
        };
        PreferencesPatch: {
            base_revision: number;
            /** @enum {string} */
            theme?: "system" | "day" | "night";
            reduced_effects?: boolean;
            reading_font_px?: number;
            /** @enum {string} */
            explanation_language?: "auto" | "zh" | "en";
            automatic_explanation?: boolean;
        };
        DeviceSession: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: uuid */
            device_id: string;
            device_name: string;
            /** Format: date-time */
            last_active_at: string;
            /** Format: date-time */
            expires_at: string;
            is_current: boolean;
            revoked_at: string | null;
        };
        TrustedDevice: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            device_name: string;
            trusted: boolean;
            revoked_at: string | null;
            key_epoch: number;
        };
        TrustDeviceRequest: {
            device_name: string;
            consent_version: string;
            /** @constant */
            trust_confirmed: true;
        };
        CacheUnlockRequest: {
            /** Format: uuid */
            device_id: string;
            key_epoch: number;
            /** @enum {string} */
            scope: "learning" | "drafts" | "recording_recovery";
            class_session_id: string | null;
        };
        CacheUnlockResult: {
            /** Format: uuid */
            key_id: string;
            key_epoch: number;
            key_material_b64: string;
            /** Format: date-time */
            expires_at: string;
            /** @enum {string} */
            scope: "learning" | "drafts" | "recording_recovery";
            cache_policy_revision: string;
        };
        Semester: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            name: string;
            academic_year: string | null;
            starts_on: string | null;
            ends_on: string | null;
            archived: boolean;
        };
        SemesterCreate: {
            name: string;
            academic_year?: string | null;
            starts_on?: string | null;
            ends_on?: string | null;
        };
        SemesterPatch: {
            base_revision: number;
            name?: string;
            academic_year?: string | null;
            starts_on?: string | null;
            ends_on?: string | null;
            archived?: boolean;
        };
        Course: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: uuid */
            semester_id: string;
            name: string;
            english_name: string;
            /** @enum {string} */
            language: "auto" | "zh" | "en" | "mixed";
            teacher: string;
            room: string;
            color_token: string;
            icon_token: string;
            archived: boolean;
            class_session_count: number;
            last_class_session_at: string | null;
        };
        CourseCreate: {
            /** Format: uuid */
            semester_id: string;
            name: string;
            english_name?: string;
            /** @enum {string} */
            language?: "auto" | "zh" | "en" | "mixed";
            teacher?: string;
            room?: string;
            color_token?: string;
            icon_token?: string;
        };
        CoursePatch: {
            base_revision: number;
            /** Format: uuid */
            semester_id?: string;
            name?: string;
            english_name?: string;
            /** @enum {string} */
            language?: "auto" | "zh" | "en" | "mixed";
            teacher?: string;
            room?: string;
            color_token?: string;
            icon_token?: string;
            archived?: boolean;
        };
        DeleteScopeRequest: {
            base_revision: number;
            /** @enum {string} */
            mode: "cascade" | "move_children";
            destination_id?: string | null;
            confirmation_token: string;
        };
        DeletionPreview: {
            /** @enum {string} */
            scope_type: "semester" | "course" | "class_session" | "audio" | "account";
            /** Format: uuid */
            scope_id: string;
            revision: number;
            class_session_count: number;
            media_bytes: number;
            confirmation_token: string;
            /** Format: date-time */
            expires_at: string;
        };
        GlossaryTerm: {
            term: string;
            meaning: string;
            /** @enum {string} */
            language: "auto" | "zh" | "en" | "mixed";
        };
        CourseMaterial: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: uuid */
            course_id: string;
            syllabus: string;
            glossary: components["schemas"]["GlossaryTerm"][];
            /** @enum {string} */
            language: "auto" | "zh" | "en" | "mixed";
            content_sha256: string;
        };
        CourseMaterialWrite: {
            base_revision: number;
            syllabus: string;
            glossary: components["schemas"]["GlossaryTerm"][];
            /** @enum {string} */
            language: "auto" | "zh" | "en" | "mixed";
        };
        FlowStates: {
            /** @enum {string} */
            capture: "idle" | "recording" | "paused" | "local_only" | "failed" | "ended";
            /** @enum {string} */
            upload: "pending" | "uploading" | "durable" | "failed";
            /** @enum {string} */
            asr: "idle" | "streaming" | "catching_up" | "draining" | "succeeded" | "failed" | "unknown";
            /** @enum {string} */
            ai: "idle" | "queued" | "running" | "succeeded" | "failed" | "unknown" | "cancel_requested" | "cancelled";
            /** @enum {string} */
            sync: "synced" | "local_only" | "syncing" | "failed" | "conflict";
        };
        ClassSession: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: uuid */
            course_id: string;
            title: string;
            /** @enum {string} */
            language: "auto" | "zh" | "en" | "mixed";
            started_at: string | null;
            capture_ended_at: string | null;
            audio_expires_at: string | null;
            adopted_transcript_version_id: string | null;
            flows: components["schemas"]["FlowStates"];
            effective_capture_ms: number;
            known_gap_count: number;
            unknown_gap_count: number;
            deleted_at: string | null;
        };
        ClassSessionCreate: {
            /** Format: uuid */
            course_id: string;
            title: string;
            /** @enum {string} */
            language?: "auto" | "zh" | "en" | "mixed";
        };
        ClassSessionPatch: {
            base_revision: number;
            /** Format: uuid */
            course_id?: string;
            title?: string;
            /** @enum {string} */
            language?: "auto" | "zh" | "en" | "mixed";
        };
        CaptureAcquire: {
            /** Format: uuid */
            device_id: string;
            microphone_consent_version: string;
            recovery_consent_version: string;
            asr_consent_version: string;
            confirm_takeover: boolean;
            expected_lease_id?: string | null;
        };
        CaptureLease: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            class_session_id: string;
            /** Format: uuid */
            capture_epoch_id: string;
            fence_token: number;
            /** Format: date-time */
            expires_at: string;
            renew_after_seconds: number;
            timeline_offset_ms: number;
            /** @constant */
            sample_rate: 16000;
            /** @constant */
            channel_count: 1;
            /** @constant */
            encoding: "pcm_s16le";
        };
        CaptureCommand: {
            /** Format: uuid */
            capture_epoch_id: string;
            fence_token: number;
            last_local_seq: number;
            last_sample: number;
            client_monotonic_ms: number;
        };
        CaptureStop: {
            /** Format: uuid */
            capture_epoch_id: string;
            fence_token: number;
            last_local_seq: number;
            last_sample: number;
            client_monotonic_ms: number;
            manifest_sha256: string;
            known_gaps: {
                start_sample: number;
                end_sample: number;
                /** @enum {string} */
                reason: "paused" | "device_lost" | "storage_failed" | "unknown";
            }[];
        };
        AudioChunkDescriptor: {
            /** Format: uuid */
            class_session_id: string;
            /** Format: uuid */
            capture_epoch_id: string;
            seq: number;
            start_sample: number;
            end_sample: number;
            byte_length: number;
            sha256: string;
            /** @constant */
            encoding: "pcm_s16le";
            /** @constant */
            sample_rate: 16000;
            /** @constant */
            channels: 1;
        };
        AudioChunkPrepare: {
            /** Format: uuid */
            capture_epoch_id: string;
            fence_token: number;
            seq: number;
            start_sample: number;
            end_sample: number;
            byte_length: number;
            sha256: string;
        };
        AudioChunkReceipt: {
            /** Format: uuid */
            id: string;
            descriptor: components["schemas"]["AudioChunkDescriptor"];
            /** @enum {string} */
            state: "pending" | "durable" | "failed";
            durable_at: string | null;
            /** @enum {string} */
            asr_submission: "not_submitted" | "submitted" | "unknown" | "completed";
            /** Format: uuid */
            receipt_id: string;
        };
        AudioManifest: {
            /** Format: uuid */
            class_session_id: string;
            /** Format: uuid */
            capture_epoch_id: string;
            sealed: boolean;
            expected_last_seq: number | null;
            durable_ranges: {
                first_seq: number;
                last_seq: number;
            }[];
            missing_ranges: {
                first_seq: number;
                last_seq: number;
            }[];
            unknown_provider_ranges: {
                start_sample: number;
                end_sample: number;
            }[];
            audio_expires_at: string | null;
        };
        RecoveryAuthorization: {
            /** Format: uuid */
            capture_epoch_id: string;
            manifest_sha256: string;
            known_last_seq: number;
            known_last_sample: number;
            confirm_retired_epoch: boolean;
        };
        RecoveryAuthorizationResult: {
            manifest: components["schemas"]["AudioManifest"];
            recovery_upload_token: string;
            /** Format: date-time */
            expires_at: string;
            /** @constant */
            asr_authorized: false;
        };
        AsrStreamCreate: {
            /** Format: uuid */
            capture_epoch_id: string;
            fence_token: number;
            consent_version: string;
            source_chunk_ids: string[];
            /** @enum {string} */
            mode: "live" | "catch_up";
        };
        AsrStream: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            class_session_id: string;
            /** Format: uuid */
            capture_epoch_id: string;
            /** Format: uuid */
            task_id: string;
            /** @enum {string} */
            state: "idle" | "streaming" | "catching_up" | "draining" | "succeeded" | "failed" | "unknown";
            /** @constant */
            provider: "aliyun";
            /** @constant */
            model: "qwen-audio-3.1-asr-flash-streaming";
            provider_request_id: string | null;
            submitted_end_sample: number;
            confirmed_end_sample: number;
        };
        TranscriptVersion: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: uuid */
            class_session_id: string;
            /** @enum {string} */
            kind: "realtime" | "retranscription" | "imported";
            /** @enum {string} */
            state: "open" | "sealed";
            source_task_id: string | null;
            adopted: boolean;
        };
        TranscriptSegment: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: uuid */
            class_session_id: string;
            /** Format: uuid */
            transcript_version_id: string;
            stream_id: string | null;
            /** Format: uuid */
            segment_revision_id: string;
            /** @enum {string} */
            kind: "machine" | "personal";
            /** @enum {string} */
            origin: "live_asr" | "retranscription" | "import" | "manual";
            /** @constant */
            status: "final";
            text: string;
            start_ms: number | null;
            end_ms: number | null;
            /** @enum {string} */
            timestamp_source: "provider_segment" | "capture_mapping" | "import_declared" | "user_declared" | "legacy_fragment" | "unknown";
            provider_request_id: string | null;
            source_chunk_ids: string[];
            adopted_revision_id: string | null;
        };
        TranscriptCorrection: {
            base_revision: number;
            text: string;
        };
        AdoptRequest: {
            base_revision: number;
            /** Format: uuid */
            revision_id: string;
        };
        AdoptTranscriptRequest: {
            base_revision: number;
            /** Format: uuid */
            transcript_version_id: string;
        };
        SourceRef: {
            /** @enum {string} */
            kind: "transcript_segment" | "slide_version" | "mark";
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            revision_id: string;
            content_sha256: string;
        };
        SourceSnapshot: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            class_session_id: string;
            snapshot_sha256: string;
            source_refs: components["schemas"]["SourceRef"][];
            start_ms: number;
            end_ms: number;
            scope_complete: boolean;
            missing_reasons: string[];
            /** Format: date-time */
            created_at: string;
        };
        MediaUploadCreate: {
            /** @enum {string} */
            purpose: "slide" | "import_package";
            class_session_id: string | null;
            /** @enum {string} */
            mime_type: "image/png" | "image/jpeg" | "image/webp" | "application/zip" | "application/json";
            byte_length: number;
            sha256: string;
            filename: string;
        };
        MediaUpload: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            asset_id: string;
            upload_path: string;
            /** @enum {string} */
            state: "staging" | "uploaded" | "verified" | "rejected";
            /** Format: date-time */
            expires_at: string;
        };
        MediaAsset: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            class_session_id: string | null;
            /** @enum {string} */
            kind: "slide" | "audio_chunk" | "export" | "import_package";
            sha256: string;
            byte_length: number;
            mime_type: string;
            /** @enum {string} */
            state: "staging" | "available" | "access_revoked" | "pending_delete" | "delete_failed" | "deleted";
            expires_at: string | null;
        };
        MediaAccess: {
            download_path: string;
            /** Format: date-time */
            expires_at: string;
            /** @constant */
            requires_session: true;
        };
        MediaVerify: {
            sha256: string;
            byte_length: number;
        };
        Slide: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: uuid */
            class_session_id: string;
            /** Format: uuid */
            asset_id: string;
            /** Format: uuid */
            version_id: string;
            title: string;
            start_ms: number;
            end_ms: number | null;
            /** @enum {string} */
            rotation: "0" | "90" | "180" | "270";
            removed: boolean;
        };
        SlideCreate: {
            /** Format: uuid */
            asset_id: string;
            title: string;
            start_ms: number;
            end_ms: number | null;
        };
        SlidePatch: {
            base_revision: number;
            title?: string;
            start_ms?: number;
            end_ms?: number | null;
            /** @enum {string} */
            rotate?: "left" | "right";
            removed?: boolean;
        };
        MarkAnchor: {
            /** @constant */
            kind: "source";
            source_ref: components["schemas"]["SourceRef"];
        } | {
            /** @constant */
            kind: "review_revision";
            /** Format: uuid */
            document_id: string;
            /** Format: uuid */
            revision_id: string;
            content_sha256: string;
        } | {
            /** @constant */
            kind: "timepoint";
            /** Format: uuid */
            capture_epoch_id: string;
            sample_offset: number;
        };
        Mark: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: uuid */
            class_session_id: string;
            /** @enum {string} */
            kind: "important" | "unclear";
            anchor: components["schemas"]["MarkAnchor"];
            note: string;
            understood: boolean;
        };
        MarkCreate: {
            /** @enum {string} */
            kind: "important" | "unclear";
            anchor: components["schemas"]["MarkAnchor"];
            note: string;
        };
        MarkPatch: {
            base_revision: number;
            note?: string;
            understood?: boolean;
        };
        GenerationPlanRequest: {
            /** @enum {string} */
            kind: "ai_question" | "ai_explanation" | "review_summary" | "asr_retranscribe";
            /** @enum {string} */
            scope: "recent30" | "recent120" | "thought_group" | "slides" | "all" | "marked" | "range";
            anchor_ms?: number;
            start_ms?: number;
            end_ms?: number;
            seen_sources: components["schemas"]["SourceRef"][];
            question?: string;
            include_images: boolean;
            card_types?: ("concept" | "derivation" | "example" | "pitfall")[];
            english_answer?: boolean;
            accept_partial_scope: boolean;
        };
        Destination: {
            provider: string;
            model: string;
            region: string;
            configuration_revision: string;
        };
        GenerationPlan: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            class_session_id: string;
            /** @enum {string} */
            kind: "ai_question" | "ai_explanation" | "review_summary" | "asr_retranscribe";
            plan_sha256: string;
            source_snapshot: components["schemas"]["SourceSnapshot"] | null;
            audio_manifest_sha256: string | null;
            audio_chunk_ids: string[];
            destination: components["schemas"]["Destination"];
            prompt_version: string;
            output_schema_version: string;
            price_version: string;
            estimated_batches: number;
            /** @description CNY microyuan (10^-6 yuan); decimal integer string, no floating point. */
            maximum_cost_microyuan: string;
            quota_units: number;
            /** Format: date-time */
            expires_at: string;
            /** @enum {string} */
            source_status: "unchanged" | "changed" | "unavailable";
            contains_images: boolean;
        };
        ExecutePlan: {
            plan_sha256: string;
            confirmed_destination_revision: string;
            /** @constant */
            confirm_send: true;
            confirm_images: boolean;
            confirm_original_snapshot: boolean;
            confirm_partial_scope: boolean;
        };
        AutomaticExplanationConsent: {
            enabled: boolean;
            max_operations: number;
            /** @description CNY microyuan (10^-6 yuan); decimal integer string, no floating point. */
            max_cost_microyuan: string;
            destination_revision: string;
            consent_version: string;
            /** @enum {string} */
            source_scope: "recent30" | "recent120" | "thought_group";
        };
        Task: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            class_session_id: string | null;
            /** @enum {string} */
            kind: "asr_live" | "asr_retranscribe" | "ai_question" | "ai_explanation" | "review_summary" | "export" | "import" | "delete";
            /** @enum {string} */
            state: "queued" | "running" | "succeeded" | "failed" | "unknown" | "cancel_requested" | "cancelled";
            attempt_id: string | null;
            parent_task_id: string | null;
            completed_units: number;
            total_units: number | null;
            error_code: string | null;
            result_id: string | null;
            source_snapshot_id: string | null;
            /** @enum {string} */
            provider_outcome: "not_submitted" | "succeeded" | "failed" | "unknown";
            /** @description CNY microyuan (10^-6 yuan); decimal integer string, no floating point. */
            maximum_cost_microyuan: string;
            settled_cost_microyuan: string | null;
            may_have_charged: boolean;
        } & unknown;
        RawProviderOutput: {
            /** Format: uuid */
            task_id: string;
            /** Format: uuid */
            attempt_id: string;
            raw_text: string;
            /** @enum {string} */
            validation_status: "valid" | "invalid" | "not_validated";
            error_code: string | null;
            /** Format: date-time */
            created_at: string;
        };
        RetryTask: {
            reason: string;
            confirm_duplicate_billing: boolean;
            /** Format: uuid */
            original_attempt_id: string;
        };
        TaskReconcile: {
            /** @enum {string} */
            state: "queued" | "running" | "succeeded" | "failed" | "unknown" | "cancel_requested" | "cancelled";
            provider_query_supported: boolean;
            /** Format: date-time */
            checked_at: string;
            /** @constant */
            new_submission: false;
        };
        ReviewCard: {
            /** @enum {string} */
            type: "concept" | "derivation" | "example" | "pitfall";
            title: string;
            front: string;
            back: string;
            supplement: string;
            english: string;
            english_meaning: string;
            source_ids: string[];
            missing: string[];
        };
        ReviewNoteSection: {
            /** @enum {string} */
            section: "structure" | "focus" | "questions" | "slides";
            title: string;
            body: string;
            supplement: string;
            source_ids: string[];
            missing: string[];
        };
        ReviewModelOutput: {
            /** @constant */
            schema_version: 1;
            cards: components["schemas"]["ReviewCard"][];
            notes: components["schemas"]["ReviewNoteSection"][];
        };
        ReviewDocument: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: uuid */
            class_session_id: string;
            /** @enum {string} */
            kind: "summary" | "card" | "note";
            title: string;
            adopted_revision_id: string | null;
            latest_personal_revision_id: string | null;
            candidate_revision_ids: string[];
            understood: boolean;
            /** @enum {string} */
            source_status: "unchanged" | "changed" | "unavailable" | "new_material_not_included";
        };
        ReviewRevision: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: uuid */
            document_id: string;
            /** @enum {string} */
            kind: "ai_candidate" | "personal";
            parent_revision_id: string | null;
            source_snapshot_id: string | null;
            raw_output_ref: string | null;
            body_markdown: string;
            structured_content: components["schemas"]["ReviewModelOutput"] | null;
            personal_note: string;
        };
        PersonalRevisionWrite: {
            base_revision: number;
            base_content_revision_id: string | null;
            /** Format: uuid */
            draft_id: string;
            body_markdown: string;
            personal_note: string;
        };
        ReviewDocumentCreate: {
            /** Format: uuid */
            class_session_id: string;
            title: string;
            body_markdown: string;
            /** Format: uuid */
            draft_id: string;
        };
        ReviewDocumentPatch: {
            base_revision: number;
            title?: string;
            understood?: boolean;
        };
        Question: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** Format: uuid */
            class_session_id: string;
            /** Format: uuid */
            task_id: string;
            /** Format: uuid */
            source_snapshot_id: string;
            question: string;
            answer_markdown: string | null;
            /** @enum {string} */
            state: "queued" | "running" | "succeeded" | "failed" | "unknown" | "cancel_requested" | "cancelled";
            understood: boolean;
        };
        QuestionPatch: {
            base_revision: number;
            understood: boolean;
        };
        SyncOperation: {
            /** Format: uuid */
            op_id: string;
            /** @constant */
            object_type: "personal_revision";
            /** Format: uuid */
            object_id: string;
            /** Format: uuid */
            draft_id: string;
            base_revision: number;
            payload: components["schemas"]["PersonalRevisionWrite"];
        } | {
            /** Format: uuid */
            op_id: string;
            /** @constant */
            object_type: "mark";
            /** Format: uuid */
            object_id: string;
            /** Format: uuid */
            draft_id: string;
            base_revision: number;
            payload: components["schemas"]["MarkPatch"];
        } | {
            /** Format: uuid */
            op_id: string;
            /** @constant */
            object_type: "preferences";
            /** Format: uuid */
            object_id: string;
            /** Format: uuid */
            draft_id: string;
            base_revision: number;
            payload: components["schemas"]["PreferencesPatch"];
        } | {
            /** Format: uuid */
            op_id: string;
            /** @constant */
            object_type: "course";
            /** Format: uuid */
            object_id: string;
            /** Format: uuid */
            draft_id: string;
            base_revision: number;
            payload: components["schemas"]["CoursePatch"];
        };
        SyncBatch: {
            operations: components["schemas"]["SyncOperation"][];
        };
        OperationReceipt: {
            /** Format: uuid */
            op_id: string;
            operation: string;
            request_sha256: string;
            /** @enum {string} */
            status: "applied" | "conflict" | "deleted" | "rejected" | "pending";
            object_id: string | null;
            revision: number | null;
            conflict_id: string | null;
            task_id: string | null;
            /** Format: date-time */
            created_at: string;
        };
        SyncResult: {
            receipts: components["schemas"]["OperationReceipt"][];
            cursor: string;
        };
        Change: {
            /** Format: uuid */
            event_id: string;
            /** @enum {string} */
            object_type: "semester" | "course" | "class_session" | "transcript" | "slide" | "question" | "review_document" | "mark" | "preferences" | "task" | "deletion" | "notification";
            /** Format: uuid */
            object_id: string;
            revision: number;
            deleted: boolean;
        };
        Changes: {
            items: components["schemas"]["Change"][];
            next_cursor: string;
            has_more: boolean;
        };
        Conflict: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            object_id: string;
            base_revision: number;
            server_revision: number;
            local_operation: components["schemas"]["SyncOperation"];
            server_content: components["schemas"]["ReviewRevision"] | components["schemas"]["Mark"] | components["schemas"]["Preferences"] | components["schemas"]["Course"];
            resolved: boolean;
        };
        ResolveConflict: {
            base_revision: number;
            /** @enum {string} */
            resolution: "edited_merge" | "copy_to_new_note" | "use_server";
            merged_operation?: components["schemas"]["SyncOperation"];
            /** Format: uuid */
            destination_class_session_id?: string;
        };
        BootstrapProjection: {
            /** @enum {string} */
            object_type: "semester" | "course" | "class_session" | "review_document" | "mark" | "preferences";
            /** Format: uuid */
            object_id: string;
            revision: number;
            parent_id: string | null;
            adopted_revision_id: string | null;
            /** Format: date-time */
            updated_at: string;
        };
        SyncBootstrap: {
            watermark: string;
            /** Format: date-time */
            generated_at: string;
            /** @constant */
            projection_complete: true;
            projections: components["schemas"]["BootstrapProjection"][];
            maximum_projection_count: number;
        };
        ExportRequest: {
            /** @enum {string} */
            scope: "account" | "course" | "class_session";
            /** Format: uuid */
            scope_id: string;
            /** @enum {string} */
            format: "json" | "markdown" | "zip";
            include_audio: boolean;
            allow_missing: boolean;
            confirmed_audio_privacy: boolean;
        };
        ImportPreviewRequest: {
            /** Format: uuid */
            upload_id: string;
            /** Format: uuid */
            destination_course_id: string;
        };
        ImportPreview: {
            /** Format: uuid */
            id: string;
            archive_sha256: string;
            class_session_count: number;
            media_bytes: number;
            warnings: string[];
            unsupported_fields: string[];
            /** Format: date-time */
            expires_at: string;
        };
        ImportCommit: {
            /** Format: uuid */
            preview_id: string;
            archive_sha256: string;
            /** @constant */
            confirm_independent_copy: true;
            allow_missing: boolean;
        };
        Deletion: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** @enum {string} */
            scope_type: "audio" | "class_session" | "course" | "semester" | "account" | "media_asset";
            /** Format: uuid */
            scope_id: string;
            /** @enum {string} */
            state: "access_revoked" | "pending_delete" | "delete_failed" | "deleted";
            /** Format: date-time */
            access_revoked_at: string;
            physical_completed_at: string | null;
            failure_code: string | null;
        };
        DeletionSubmit: {
            confirmation_token: string;
            base_revision: number;
        };
        QuotaBucket: {
            /** @enum {string} */
            kind: "asr_month_ms" | "asr_day_ms" | "retranscribe_month_ms" | "interactive_month_count" | "summary_month_count";
            /** Format: date-time */
            period_start: string;
            /** Format: date-time */
            period_end: string;
            limit: number;
            reserved: number;
            submitted: number;
            settled: number;
            unknown: number;
            remaining: number;
        };
        QuotaView: {
            buckets: components["schemas"]["QuotaBucket"][];
            /** @enum {string} */
            platform_billing_state: "normal" | "warning60" | "warning80" | "blocked100" | "paused";
            asr_enabled: boolean;
            ai_enabled: boolean;
        };
        SearchResult: {
            /** @enum {string} */
            object_type: "course" | "class_session" | "transcript_segment" | "review_document" | "card";
            /** Format: uuid */
            object_id: string;
            class_session_id: string | null;
            title: string;
            snippet: string;
            revision_id: string | null;
            locator: string;
        };
        Notification: {
            /** Format: uuid */
            id: string;
            /** @enum {string} */
            kind: "task_completed" | "conflict" | "audio_expiry" | "capture_failure" | "save_failure" | "security";
            /** @enum {string} */
            severity: "info" | "warning" | "critical";
            object_id: string | null;
            read_at: string | null;
            resolved_at: string | null;
            /** Format: date-time */
            created_at: string;
            message_code: string;
        };
        NotificationRead: {
            /** @constant */
            read: true;
        };
        Feedback: {
            /** @enum {string} */
            category: "bug" | "asr" | "ai_content" | "ui" | "sync" | "security" | "other";
            build_id: string;
            description: string;
            request_id: string | null;
            confirmed_excerpt: string | null;
            confirm_send_excerpt: boolean;
        };
        Invitation: {
            /** Format: uuid */
            id: string;
            revision: number;
            /** Format: date-time */
            created_at: string;
            /** Format: date-time */
            updated_at: string;
            /** @enum {string} */
            kind: "email_bound" | "single_use";
            email_hint: string | null;
            /** @enum {string} */
            state: "issued" | "consumed" | "revoked" | "expired";
            /** Format: date-time */
            expires_at: string;
        };
        InvitationCreate: {
            /** @enum {string} */
            kind: "email_bound" | "single_use";
            /** Format: email */
            email?: string;
        };
        InvitationCreated: {
            invitation: components["schemas"]["Invitation"];
            one_time_token: string;
        };
        AdminUserPatch: {
            base_revision: number;
            /** @enum {string} */
            status: "active" | "disabled";
            reason: string;
        };
        QuotaAdjustment: {
            /** Format: uuid */
            user_id: string;
            /** @enum {string} */
            bucket_kind: "asr_month_ms" | "asr_day_ms" | "retranscribe_month_ms" | "interactive_month_count" | "summary_month_count";
            /** Format: date-time */
            period_start: string;
            additional_units: number;
            reason: string;
        };
        AdminSwitches: {
            revision: number;
            registration_enabled: boolean;
            asr_enabled: boolean;
            ai_enabled: boolean;
            /** @enum {string} */
            max_active_users: "10" | "30";
        };
        AdminSwitchesPatch: {
            base_revision: number;
            registration_enabled?: boolean;
            asr_enabled?: boolean;
            ai_enabled?: boolean;
            /** @enum {string} */
            max_active_users?: "10" | "30";
            reason: string;
        };
        AdminUsage: {
            /** Format: date-time */
            period_start: string;
            /** Format: date-time */
            period_end: string;
            /** @description CNY microyuan (10^-6 yuan); decimal integer string, no floating point. */
            budget_microyuan: string;
            /** @description CNY microyuan (10^-6 yuan); decimal integer string, no floating point. */
            fixed_and_accrued_microyuan: string;
            /** @description CNY microyuan (10^-6 yuan); decimal integer string, no floating point. */
            settled_microyuan: string;
            /** @description CNY microyuan (10^-6 yuan); decimal integer string, no floating point. */
            reserved_microyuan: string;
            /** @description CNY microyuan (10^-6 yuan); decimal integer string, no floating point. */
            unknown_microyuan: string;
            /** @description CNY microyuan (10^-6 yuan); decimal integer string, no floating point. */
            safety_reserve_microyuan: string;
            price_version: string;
            ledger_reconciled_at: string | null;
        };
        AdminHealth: {
            /** Format: date-time */
            checked_at: string;
            /** @enum {string} */
            database: "healthy" | "degraded" | "unavailable";
            /** @enum {string} */
            objects: "healthy" | "degraded" | "unavailable";
            worker_heartbeat_at: string | null;
            oldest_queued_seconds: number;
            unknown_task_count: number;
            pending_delete_count: number;
            failed_delete_count: number;
            active_asr_streams: number;
            last_backup_at: string | null;
        };
        AuditRecord: {
            /** Format: uuid */
            id: string;
            /** Format: uuid */
            actor_id: string;
            operation: string;
            target_type: string;
            target_id: string | null;
            reason: string;
            /** Format: date-time */
            created_at: string;
        };
        SecurityEvent: {
            /** Format: uuid */
            id: string;
            /** @enum {string} */
            kind: "login" | "login_failed" | "password_changed" | "password_reset" | "email_changed" | "mfa_changed" | "session_revoked" | "device_revoked";
            device_name: string;
            /** Format: date-time */
            created_at: string;
        };
        WsTicketRequest: {
            /** @enum {string} */
            channel: "events" | "capture";
            class_session_id: string | null;
            capture_epoch_id: string | null;
            fence_token: number | null;
        };
        WsTicket: {
            ticket: string;
            /** Format: date-time */
            expires_at: string;
            /** @enum {string} */
            path: "/api/web/v1/ws/events" | "/api/web/v1/ws/capture";
            /** @constant */
            subprotocol: "classroom.v1";
        };
        DeviceSessionPage: {
            items: components["schemas"]["DeviceSession"][];
            next_cursor: string | null;
        };
        SecurityEventPage: {
            items: components["schemas"]["SecurityEvent"][];
            next_cursor: string | null;
        };
        TrustedDevicePage: {
            items: components["schemas"]["TrustedDevice"][];
            next_cursor: string | null;
        };
        SemesterPage: {
            items: components["schemas"]["Semester"][];
            next_cursor: string | null;
        };
        CoursePage: {
            items: components["schemas"]["Course"][];
            next_cursor: string | null;
        };
        CourseMaterialPage: {
            items: components["schemas"]["CourseMaterial"][];
            next_cursor: string | null;
        };
        ClassSessionPage: {
            items: components["schemas"]["ClassSession"][];
            next_cursor: string | null;
        };
        TranscriptVersionPage: {
            items: components["schemas"]["TranscriptVersion"][];
            next_cursor: string | null;
        };
        TranscriptSegmentPage: {
            items: components["schemas"]["TranscriptSegment"][];
            next_cursor: string | null;
        };
        SlidePage: {
            items: components["schemas"]["Slide"][];
            next_cursor: string | null;
        };
        MarkPage: {
            items: components["schemas"]["Mark"][];
            next_cursor: string | null;
        };
        TaskPage: {
            items: components["schemas"]["Task"][];
            next_cursor: string | null;
        };
        QuestionPage: {
            items: components["schemas"]["Question"][];
            next_cursor: string | null;
        };
        ReviewDocumentPage: {
            items: components["schemas"]["ReviewDocument"][];
            next_cursor: string | null;
        };
        ReviewRevisionPage: {
            items: components["schemas"]["ReviewRevision"][];
            next_cursor: string | null;
        };
        SearchResultPage: {
            items: components["schemas"]["SearchResult"][];
            next_cursor: string | null;
        };
        NotificationPage: {
            items: components["schemas"]["Notification"][];
            next_cursor: string | null;
        };
        UserPage: {
            items: components["schemas"]["User"][];
            next_cursor: string | null;
        };
        InvitationPage: {
            items: components["schemas"]["Invitation"][];
            next_cursor: string | null;
        };
        AuditRecordPage: {
            items: components["schemas"]["AuditRecord"][];
            next_cursor: string | null;
        };
    };
    responses: never;
    parameters: {
        IdempotencyKey: string;
        /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
        CsrfToken: string;
        ReauthToken: string;
    };
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    getMeta: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Meta"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getCsrf: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CsrfBootstrap"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    register: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RegisterRequest"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    verifyEmail: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TokenRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    resendVerification: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EmailRequest"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    login: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoginRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LoginResult"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getSessionContext: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionContext"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    completeLoginMfa: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MfaChallengeRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LoginResult"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    logout: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    logoutAll: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    reauthenticate: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReauthRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReauthResult"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    forgotPassword: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EmailRequest"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    resetPassword: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PasswordResetRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    changePassword: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PasswordChangeRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    requestEmailChange: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EmailChangeRequest"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    confirmEmailChange: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TokenRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    enrollMfa: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MfaEnrollment"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    confirmMfa: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MfaEnrollmentConfirm"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecoveryCodes"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    disableMfa: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    rotateRecoveryCodes: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecoveryCodes"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getMe: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["User"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    updateProfile: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProfilePatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["User"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getPreferences: {
        parameters: {
            query?: {
                revision?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Preferences"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    updatePreferences: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PreferencesPatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Preferences"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getQuota: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuotaView"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listSessions: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeviceSessionPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listSecurityEvents: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SecurityEventPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    revokeSession: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listDevices: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TrustedDevicePage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    trustDevice: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TrustDeviceRequest"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TrustedDevice"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    revokeDevice: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path: {
                device_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    unlockCache: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CacheUnlockRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CacheUnlockResult"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    previewAccountDeletion: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeletionPreview"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    deleteAccount: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeletionSubmit"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Deletion"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listSemesters: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SemesterPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createSemester: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SemesterCreate"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Semester"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getSemester: {
        parameters: {
            query?: {
                revision?: number;
            };
            header?: never;
            path: {
                semester_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Semester"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    updateSemester: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                semester_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SemesterPatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Semester"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    previewSemesterDeletion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                semester_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeletionPreview"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    deleteSemester: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                semester_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeleteScopeRequest"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Deletion"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listCourses: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
                semester_id?: string;
                archived?: boolean;
                q?: string;
                sort?: "recent" | "name";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CoursePage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createCourse: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CourseCreate"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Course"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getCourse: {
        parameters: {
            query?: {
                /** @description Exact committed revision for bootstrap hydration; absent returns latest. Tombstone/authorization still checked. Missing historical revision returns 409, never substitutes newer content. */
                revision?: number;
            };
            header?: never;
            path: {
                course_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Course"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    updateCourse: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                course_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CoursePatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Course"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    previewCourseDeletion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                course_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeletionPreview"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    deleteCourse: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                course_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeleteScopeRequest"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Deletion"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listCourseMaterials: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path: {
                course_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CourseMaterialPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    writeCourseMaterial: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                course_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CourseMaterialWrite"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CourseMaterial"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listClassSessions: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
                course_id?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClassSessionPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createClassSession: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ClassSessionCreate"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClassSession"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getClassSession: {
        parameters: {
            query?: {
                /** @description Exact committed revision for bootstrap hydration; absent returns latest. Tombstone/authorization still checked. Missing historical revision returns 409, never substitutes newer content. */
                revision?: number;
            };
            header?: never;
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClassSession"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    updateClassSession: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ClassSessionPatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClassSession"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    previewClassSessionDeletion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeletionPreview"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    deleteClassSession: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeletionSubmit"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Deletion"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    previewAudioDeletion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DeletionPreview"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    deleteAudio: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeletionSubmit"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Deletion"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    acquireCapture: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CaptureAcquire"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CaptureLease"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    renewCapture: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CaptureCommand"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CaptureLease"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    pauseCapture: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CaptureCommand"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CaptureLease"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    resumeCapture: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CaptureCommand"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CaptureLease"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    stopCapture: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CaptureStop"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AudioManifest"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    authorizeRecovery: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RecoveryAuthorization"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RecoveryAuthorizationResult"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getAudioManifest: {
        parameters: {
            query?: {
                capture_epoch_id?: string;
            };
            header?: never;
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AudioManifest"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    prepareAudioChunk: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                /** @description Required for a fenced/retired epoch; authorization binds owner/epoch/manifest/range and is upload-only. */
                "X-Recovery-Upload-Token"?: string;
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AudioChunkPrepare"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AudioChunkReceipt"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    putAudioChunk: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "Content-Digest": string;
            };
            path: {
                chunk_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/octet-stream": string;
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AudioChunkReceipt"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createAsrStream: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AsrStreamCreate"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AsrStream"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getAsrStream: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                stream_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AsrStream"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    finishAsrStream: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                stream_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AsrStream"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listTranscriptVersions: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TranscriptVersionPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listTranscriptSegments: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path: {
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TranscriptSegmentPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    correctTranscript: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                segment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TranscriptCorrection"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TranscriptSegment"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adoptTranscriptCorrection: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                segment_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AdoptRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TranscriptSegment"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adoptTranscriptVersion: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AdoptTranscriptRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClassSession"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createMediaUpload: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MediaUploadCreate"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MediaUpload"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    putMediaUpload: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                upload_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/octet-stream": string;
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MediaUpload"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    completeMediaUpload: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                upload_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MediaVerify"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MediaAsset"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    issueMediaAccess: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                asset_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MediaAccess"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    downloadMedia: {
        parameters: {
            query: {
                ticket: string;
            };
            header?: never;
            path: {
                asset_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/octet-stream": string;
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listSlides: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SlidePage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createSlide: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SlideCreate"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Slide"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    updateSlide: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                slide_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SlidePatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Slide"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listMarks: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MarkPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createMark: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MarkCreate"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Mark"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getMark: {
        parameters: {
            query?: {
                revision?: number;
            };
            header?: never;
            path: {
                mark_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Mark"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    updateMark: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                mark_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MarkPatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Mark"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    prepareGeneration: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GenerationPlanRequest"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GenerationPlan"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getGenerationPlan: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                plan_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GenerationPlan"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    executeGeneration: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                plan_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExecutePlan"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Task"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    setAutomaticExplanation: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                class_session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AutomaticExplanationConsent"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listTasks: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
                class_session_id?: string;
                state?: "queued" | "running" | "succeeded" | "failed" | "unknown" | "cancel_requested" | "cancelled";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TaskPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getTask: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                task_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Task"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getRawProviderOutput: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                task_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RawProviderOutput"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    reconcileTask: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                task_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TaskReconcile"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    retryTask: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                task_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RetryTask"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Task"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    cancelTask: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                task_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Task"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getSourceSnapshot: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                snapshot_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourceSnapshot"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listQuestions: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
                class_session_id?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuestionPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    updateQuestion: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                question_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QuestionPatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Question"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listReviewDocuments: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
                semester_id?: string;
                course_id?: string;
                class_session_id?: string;
                kind?: "summary" | "card" | "note";
                understood?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewDocumentPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createPersonalNote: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReviewDocumentCreate"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewDocument"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getReviewDocument: {
        parameters: {
            query?: {
                /** @description Exact committed revision for bootstrap hydration; absent returns latest. Tombstone/authorization still checked. Missing historical revision returns 409, never substitutes newer content. */
                revision?: number;
            };
            header?: never;
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewDocument"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    updateReviewDocument: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReviewDocumentPatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewDocument"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listReviewRevisions: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewRevisionPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    writePersonalRevision: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PersonalRevisionWrite"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewRevision"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adoptReviewRevision: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AdoptRequest"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewDocument"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    syncOperations: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SyncBatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SyncResult"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getChanges: {
        parameters: {
            query?: {
                after?: string;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Changes"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    bootstrapSync: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SyncBootstrap"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getOperationReceipt: {
        parameters: {
            query: {
                operation: string;
            };
            header?: never;
            path: {
                op_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OperationReceipt"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getConflict: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conflict_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Conflict"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    resolveConflict: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                conflict_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ResolveConflict"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OperationReceipt"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createExport: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExportRequest"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Task"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    previewImport: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ImportPreviewRequest"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportPreview"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    commitImport: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ImportCommit"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Task"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getDeletion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                deletion_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Deletion"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    search: {
        parameters: {
            query: {
                cursor?: string;
                limit?: number;
                q: string;
                course_id?: string;
                class_session_id?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SearchResultPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    listNotifications: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["NotificationPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    readNotification: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path: {
                notification_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NotificationRead"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Notification"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    submitFeedback: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Feedback"];
            };
        };
        responses: {
            /** @description Success */
            202: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Acknowledged"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminListUsers: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UserPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminUpdateUser: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path: {
                user_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AdminUserPatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["User"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminGetUserQuota: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                user_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuotaView"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminListInvitations: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvitationPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminCreateInvitation: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InvitationCreate"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvitationCreated"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminRevokeInvitation: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path: {
                invitation_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Invitation"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminReissueInvitation: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path: {
                invitation_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Empty"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvitationCreated"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminAdjustQuota: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QuotaAdjustment"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QuotaView"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminGetUsage: {
        parameters: {
            query?: {
                period_start?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminUsage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminGetHealth: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminHealth"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminGetSwitches: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminSwitches"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminUpdateSwitches: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
                "X-Reauth-Token": components["parameters"]["ReauthToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AdminSwitchesPatch"];
            };
        };
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminSwitches"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    adminListAudit: {
        parameters: {
            query?: {
                cursor?: string;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AuditRecordPage"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    createWsTicket: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": components["parameters"]["IdempotencyKey"];
                /** @description Bound to session or public preauth session; Origin/Fetch-Metadata also checked. */
                "X-CSRF-Token": components["parameters"]["CsrfToken"];
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WsTicketRequest"];
            };
        };
        responses: {
            /** @description Success */
            201: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WsTicket"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
    getReviewRevision: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                revision_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Success */
            200: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewRevision"];
                };
            };
            /** @description Typed failure; no private content in error messages. */
            default: {
                headers: {
                    "Cache-Control"?: "no-store";
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Error"];
                };
            };
        };
    };
}
