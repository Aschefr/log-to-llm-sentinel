/* Clés d'accès (WebAuthn) : conversions base64url faites ici plutôt qu'avec PublicKeyCredential.parse*FromJSON et
   toJSON(), absents de certains navigateurs et de gestionnaires comme Bitwarden qui remplacent navigator.credentials. */
(function () {
    'use strict';

    function toBytes(text) {
        const b64 = text.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((text.length + 3) % 4);
        return Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    }

    function toText(buffer) {
        if (!buffer) return null;
        const bytes = new Uint8Array(buffer);
        let s = '';
        bytes.forEach(b => { s += String.fromCharCode(b); });
        return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    /** Raison pour laquelle cette page ne peut pas utiliser de clé d'accès, ou null. */
    function unavailableReason() {
        const host = location.hostname;
        if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.includes(':') || host.startsWith('[')) return 'ip';
        if (!window.isSecureContext) return 'https';
        if (!window.PublicKeyCredential || !navigator.credentials) return 'browser';
        return null;
    }

    async function create(options) {
        const publicKey = Object.assign({}, options, {
            challenge: toBytes(options.challenge),
            user: Object.assign({}, options.user, { id: toBytes(options.user.id) }),
            excludeCredentials: (options.excludeCredentials || []).map(c => Object.assign({}, c, { id: toBytes(c.id) })),
        });
        const cred = await navigator.credentials.create({ publicKey });
        const r = cred.response;
        return {
            id: cred.id,
            rawId: toText(cred.rawId),
            type: cred.type,
            response: {
                clientDataJSON: toText(r.clientDataJSON),
                attestationObject: toText(r.attestationObject),
                transports: typeof r.getTransports === 'function' ? r.getTransports() : [],
            },
            clientExtensionResults: typeof cred.getClientExtensionResults === 'function' ? cred.getClientExtensionResults() : {},
            authenticatorAttachment: cred.authenticatorAttachment || null,
        };
    }

    async function get(options) {
        const publicKey = Object.assign({}, options, {
            challenge: toBytes(options.challenge),
            allowCredentials: (options.allowCredentials || []).map(c => Object.assign({}, c, { id: toBytes(c.id) })),
        });
        const cred = await navigator.credentials.get({ publicKey });
        const r = cred.response;
        return {
            id: cred.id,
            rawId: toText(cred.rawId),
            type: cred.type,
            response: {
                clientDataJSON: toText(r.clientDataJSON),
                authenticatorData: toText(r.authenticatorData),
                signature: toText(r.signature),
                userHandle: toText(r.userHandle),
            },
            clientExtensionResults: typeof cred.getClientExtensionResults === 'function' ? cred.getClientExtensionResults() : {},
            authenticatorAttachment: cred.authenticatorAttachment || null,
        };
    }

    window.sentinelPasskey = { create, get, unavailableReason };
})();
