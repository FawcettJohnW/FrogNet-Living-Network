/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
#include "version.hpp"
#include <cstdlib>
#include <iostream>
#include <string>
#include "frogram.hpp"
int main(){
 try {
  frogram::Session s(getenv("RIBBIT_RAM_HOST")?getenv("RIBBIT_RAM_HOST"):"127.0.0.1",getenv("RIBBIT_RAM_PORT")?atoi(getenv("RIBBIT_RAM_PORT")):8788,LISPER_API); frogram::Memory m(s,LISPER_API);
  auto cells=m.read("lisp","site|0||/24","198.19.2.0/24");
  if(cells.size()!=1){std::cerr<<"expected one site cell, got "<<cells.size()<<"\n";return 2;}
  auto &b=cells[0].bag;
  if(b["password"].type!=frogram::Json::Null){std::cerr<<"password field leaked\n";return 3;}
  if(b["prefix"].s!="198.19.2.0/24"){std::cerr<<"policy prefix missing\n";return 4;}
  std::cout<<"site_secret_absent=PASS\n";
  return 0;
 } catch(const std::exception&x){std::cerr<<x.what()<<"\n";return 1;}
}
