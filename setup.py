from setuptools import setup

setup(name='ifictionbot',
      version='0.1',
      description='Interactive fiction telegram bot',
      url='https://github.com/ykrivopalov/ifictionbot',
      author='Yury Krivopalov',
      author_email='ykrivopalov@yandex.ru',
      license='GPL3',
      packages=['ifictionbot'],
      install_requires=["telepot >= 9.0, <= 10.0"],
      zip_safe=True)
