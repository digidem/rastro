# Rastro — imagem do broker MQTT (mosquitto) autoconfigurada por variáveis de ambiente
# ou arquivos montados (F3a). Build context: broker/
#   docker build -t rastro-broker broker/
# Não precisa de volume de configuração: o entrypoint renderiza os modelos em /rastro/
# a partir do ambiente e executa o mosquitto. CapRover: apontar o build para broker/.
#
# Pin por digest (imutável): binário mosquitto 2.1 verificado na bancada; a tag 2.1.x
# não existe no registry. O conf depende de comportamentos do 2.1 (comentário inline
# rejeitado). Atualizar = decisão consciente + re-teste.
FROM eclipse-mosquitto:2@sha256:38c0da4f2ef84284d47b3b3eeea1cb3bdeabe81ee10caf0cd5c5ff61ee3ea408

# Nada de segredo na imagem: só modelos e o entrypoint.
COPY mosquitto.conf.tmpl aclfile.tmpl entrypoint.sh /rastro/
# Modos explícitos (independem do modo dos arquivos no host e funcionam com qualquer
# builder, sem depender de recurso específico de BuildKit): modelos 0444, script 0555.
RUN chmod 0444 /rastro/mosquitto.conf.tmpl /rastro/aclfile.tmpl && chmod 0555 /rastro/entrypoint.sh
# /mosquitto/data: persistência (o volume montado precisa ter a mesma posse);
# /mosquitto/secrets: destino dos certificados montados (somente leitura).
# /mosquitto/data/pki: PKI privada do modo TLS automático (chaves; fica no volume de dados).
# /mosquitto/public: volume COMPARTILHADO com ingest/web — recebe só o ca.crt público.
RUN apk add --no-cache openssl \
 && mkdir -p /mosquitto/data /mosquitto/secrets /mosquitto/public \
 && chown 1883:1883 /mosquitto/data /mosquitto/public && chmod 0755 /mosquitto/public
USER 1883:1883
EXPOSE 8883
ENTRYPOINT ["/rastro/entrypoint.sh"]
# o CMD herdado da imagem base não se aplica (o entrypoint ignora argumentos)
CMD []
