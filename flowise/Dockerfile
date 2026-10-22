FROM flowiseai/flowise@sha256:3922767afb52a5777759fd8b28a3c9eee864daea96018a791f2429eae2a76571
USER root
COPY patch-structured-output.cjs /tmp/sts-patch-structured-output.cjs
RUN node /tmp/sts-patch-structured-output.cjs && rm /tmp/sts-patch-structured-output.cjs
USER node
