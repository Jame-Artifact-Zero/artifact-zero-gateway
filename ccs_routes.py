# ccs_routes.py — AZ-CCS Color Standard page routes
# Imported by app.py to register glossary + spec + eval report pages

from flask import Blueprint, render_template

ccs = Blueprint("ccs", __name__)


@ccs.route("/glossary")
def glossary():
    return render_template("glossary.html")


@ccs.route("/ccs")
def ccs_spec():
    return render_template("ccs.html")


@ccs.route("/ccs-eval")
def ccs_eval():
    return render_template("ccs-eval.html")


def init_ccs(app):
    app.register_blueprint(ccs)
    print("[CCS] Routes live: /glossary /ccs /ccs-eval")
