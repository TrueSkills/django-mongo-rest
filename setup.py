from setuptools import setup, find_packages
import sys, os


__version__ = '0.1'
__description__ = 'Django support for REST apis using django and mongoengine',
__author__ = 'Erez Arnon',
__email__ = 'erezarnon@yahoo.com',
__license__ = 'MIT'

sys.path.insert(0, os.path.dirname(__file__))


setup(
    name='django-mongo-rest',
    version=__version__,
    url='https://github.com/TrueSkills/django-mongo-rest',
    download_url='https://github.com/TrueSkills/django-mongo-rest/tarball/master',
    license=__license__,
    author=__author__,
    author_email=__email__,
    description=__description__,
    packages=['django_mongo_rest'],
)

