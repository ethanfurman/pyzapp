.PHONY: create

create:
	# removing existing pyzapp.pyz
	# rm pyzapp.pyz
	# creating new version
	python2.7 -m pyzapp create pyzapp -f

install:
	# local
	sudo cp pyzapp.pyz /usr/local/bin/pyzapp

install-remote:
	# fal-oe
	scp pyzapp.pyz root@fal-oe:/usr/local/bin/pyzapp
	# fal-odoo
	scp pyzapp.pyz root@fal-odoo:/usr/local/bin/pyzapp
	# whc-oe
	scp pyzapp.pyz root@whc-oe:/usr/local/bin/pyzapp

update:
	# refreshing dependencies
	for d in aenum antipathy dbf scription stonemark xaml; do echo $$d && rsync --existing /source/virtualenv/lib/py-packages/$$d/* pyzapp/$$d; done
