NULL =

profiledir = $(IPA_DATA_DIR)/csrgen/profiles
profile_DATA =				\
	profiles/caIPAserviceCert.json	\
	profiles/userCert.json		\
	$(NULL)

ruledir = $(IPA_DATA_DIR)/csrgen/rules
rule_DATA =				\
	rules/dataDNS.json		\
	rules/dataEmail.json		\
	rules/dataHostCN.json		\
	rules/dataUsernameCN.json	\
	rules/dataSubjectBase.json	\
	rules/syntaxSAN.json		\
	rules/syntaxSubject.json	\
	$(NULL)

templatedir = $(IPA_DATA_DIR)/csrgen/templates
template_DATA =			\
	templates/certutil_base.tmpl	\
	templates/openssl_base.tmpl	\
	templates/openssl_macros.tmpl	\
	$(NULL)

EXTRA_DIST =				\
	$(profile_DATA)			\
	$(rule_DATA)			\
	$(template_DATA)		\
	$(NULL)

MAINTAINERCLEANFILES =			\
	*~				\
	Makefile.in
