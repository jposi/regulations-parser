from fabric.api import local, settings, abort, run, cd, sudo, prompt
from fabric.decorators import task

def run_tests():
    local('python -m unittest tests.external_citation_parse')